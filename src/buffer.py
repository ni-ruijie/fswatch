# Use a delay queue to hold inotify events for `delayed` seconds,
#   during which, some isolated events can be paired as one.
# Ref: watch_dog.observers.inotify_buffer and watch_dog.utils.delayed_queue

from threading import Thread, Lock, Condition, Event
from time import time, sleep
from collections import deque
from typing import Callable, Deque, Generic, Optional, Tuple, TypeVar, Iterable
from linux import *
from event import ExtendedInotifyConstants, InotifyEvent, ExtendedEvent
import settings


T = TypeVar("T")


class DelayedQueue(Generic[T]):
    def __init__(self, delay):
        self.delay_sec = delay
        self._lock = Lock()
        self._not_empty = Condition(self._lock)
        self._queue: Deque[Tuple[T, float, bool]] = deque()
        self._closed = False

    def put(self, element: T, delay: bool = False) -> None:
        """Add element to queue."""
        self._lock.acquire()
        self._queue.append((element, time(), delay))
        self._not_empty.notify()
        self._lock.release()

    def close(self):
        """Close queue, indicating no more items will be added."""
        self._closed = True
        # Interrupt the blocking _not_empty.wait() call in get
        self._not_empty.acquire()
        self._not_empty.notify()
        self._not_empty.release()

    def get(self) -> Optional[T]:
        """Remove and return an element from the queue, or this queue has been
        closed raise the Closed exception.
        """
        while True:
            # wait for element to be added to queue
            self._not_empty.acquire()
            while len(self._queue) == 0 and not self._closed:
                self._not_empty.wait()

            if self._closed:
                self._not_empty.release()
                return None
            head, insert_time, delay = self._queue[0]
            self._not_empty.release()

            # wait for delay if required
            if delay:
                time_left = insert_time + self.delay_sec - time()
                while time_left > 0:
                    sleep(time_left)
                    time_left = insert_time + self.delay_sec - time()

            # return element if it's still in the queue
            with self._lock:
                if len(self._queue) > 0 and self._queue[0][0] is head:
                    self._queue.popleft()
                    return head

    def remove(self, predicate: Callable[[T], bool]) -> Optional[T]:
        """Remove and return the first items for which predicate is True,
        ignoring delay."""
        with self._lock:
            for i, (elem, t, delay) in enumerate(self._queue):
                if predicate(elem):
                    del self._queue[i]
                    return elem
        return None


class InotifyBuffer(Thread):
    def __init__(self, read_raw_events: Callable) -> None:
        super().__init__()
        self._queue = DelayedQueue(settings.buffer_queue_delay)
        self._read_raw_events = read_raw_events
        self._stopped_event = Event()

    def read_event(self) -> InotifyEvent:
        if not self.is_alive():
            raise BufferError('Attemping to read buffer from an inactive thread')
        return self._queue.get()

    def run(self) -> None:
        while not self._stopped_event.is_set():
            raw_events = self._read_raw_events()
            grouped_events = self._group_events(raw_events)
            for event in grouped_events:
                delay = False
                if not event._mask & ExtendedInotifyConstants.EX_ALL_EX_EVENTS:
                    if event.lsb == InotifyConstants.IN_MOVED_FROM:
                        delay = True
                    elif event.lsb == InotifyConstants.IN_MODIFY:
                        delay = True
                self._queue.put(event, delay)

    @staticmethod
    def _group_events(event_list: Iterable[InotifyEvent]) -> Iterable[InotifyEvent]:
        grouped = []
        for e in event_list:
            if e.lsb == InotifyConstants.IN_MOVED_TO:
                for index, e0 in enumerate(grouped):
                    if e0.lsb == InotifyConstants.IN_MOVED_FROM:
                        grouped[index] = ExtendedEvent.from_other(
                            e0, mask=ExtendedInotifyConstants.EX_RENAME|InotifyConstants.IN_MOVED_TO,
                            dest_path=e._src_path)
                    break
                else:  # TODO: check self.queue
                    grouped.append(e)
            elif e.lsb == InotifyConstants.IN_MODIFY:
                for index, e0 in enumerate(grouped):
                    if e0.lsb == InotifyConstants.IN_MODIFY:
                        grouped[index] = ExtendedEvent.from_other(
                            e0, mask=ExtendedInotifyConstants.EX_BEGIN_MODIFY,
                            dest_path=e._src_path)
                    break
                else:  # TODO: check self.queue
                    grouped.append(e)
            else:
                grouped.append(e)
        return grouped

    def stop(self) -> None:
        self._stopped_event.set()