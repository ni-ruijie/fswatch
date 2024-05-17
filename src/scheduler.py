from threading import Thread, Lock, Event
from time import time
from datetime import datetime
from collections import deque
from typing import Union, Iterable, Callable, Hashable, Any
from loguru import logger


EPS = 1e-8


class BaseMeter:
    def __init__(self) -> None:
        self._prev = None

    def update(self, value: Union[int, float] = None) -> None:
        pass

    def get_prev(self) -> dict:
        if self._prev is None:
            self.get()
        else:
            self.update()
        return self._prev

    def get(self) -> dict:
        pass
    

class SlidingAverageMeter(BaseMeter):
    def __init__(self, duration: Union[int, float]) -> None:
        super().__init__()
        self._queue = deque()
        self._duration = duration

    @property
    def duration(self):
        return self._duration

    def reset_duration(self, duration: Union[int, float]) -> None:
        if duration > self._duration:
            pass  # TODO: increase duration by time
        self._duration = duration

    def update(self, value: Union[int, float] = None) -> None:
        now = time()
        if value is not None:
            self._queue.append((now, value))
        while self._queue and self._queue[0][0] <= now - self._duration:
            self._queue.popleft()

    def get(self) -> dict:
        self.update()
        tot = sum(x[1] for x in self._queue)
        avg = tot / (len(self._queue) + EPS)
        self._prev = {'sum': tot, 'avg': avg}
        return self._prev


class HistogramMeter(BaseMeter):
    def __init__(self, key: str) -> None:
        super().__init__()
        self._data = {}
        self._key = key
        self._cnt = 0
        self._tic = self._toc = time()

    def update(self, value = None) -> None:
        self._toc = time()
        if value is not None:
            k = value[self._key]
            if k not in self._data:
                self._data[k] = []
            self._data[k].append(value)
            self._cnt += 1

    def get(self) -> dict:
        self.update()
        self._prev = {
            'from_time': datetime.fromtimestamp(self._tic),
            'to_time': datetime.fromtimestamp(self._toc),
            'all_data': self._data,
            'histogram': {k: len(self._data[k]) for k in self._data},
            'count': self._cnt
        }
        self._data = {}
        self._cnt = 0
        self._tic = self._toc
        return self._prev
    
    @property
    def size(self) -> int:
        return self._cnt
    

class BaseScheduler(Thread):
    def __init__(self, callback: Callable) -> None:
        super().__init__()

        self._callback = callback
        self.route = None


class IntervalScheduler(BaseScheduler):
    """
    Schedules the frequency of messages dynamically.

    Parameters
    ----------
    callback: function
        Called every interval. Returns a priority value ranged.
        A negative priority increase the interval while a positive one does
        the opposite.

    init_interval: int
        The initial interval duration.
    """
    def __init__(self, callback: Callable[[], float], init_interval: int,
                 min_interval: int = None, max_interval: int = None,
                 stats: Iterable[SlidingAverageMeter] = None) -> None:
        super().__init__(callback)

        self._interval = init_interval
        self._min_interval = init_interval if min_interval is None else min_interval
        self._max_interval = init_interval if max_interval is None else max_interval
        if self._interval < self._min_interval or self._interval > self._max_interval \
                or self._min_interval < 1:
            raise ValueError("Bad interval values")
        self._stats = stats or []

        self._lock = Lock()
        self._stopped_event = Event()
        self._cur_time = 0

    def start(self) -> None:
        self._cur_time = time()
        super().start()

    def run(self) -> None:
        timeout = self._interval
        while not self._stopped_event.is_set():
            if not self._stopped_event.wait(timeout):
                self._cur_time = time()
                priority = self._callback()
                _prev_interval = self._interval
                self.scale_interval(2**(-priority))
                if self._interval != _prev_interval:
                    logger.debug(f'{self} Interval {_prev_interval} -> {self._interval}')

                now = time()
                timeout = self._cur_time + self._interval - now
                # XXX: Only in case callback takes more than an interval to complete
                if timeout <= 0:
                    logger.error(f'{self} Negative timeout {timeout}')
                    timeout = self._min_interval


    def stop(self) -> None:
        self._stopped_event.set()

    def _update_stats(self) -> None:
        for stat in self._stats:
            stat.reset_duration(self._interval)

    def scale_interval(self, scale: float) -> int:
        self._interval = min(self._max_interval, max(self._min_interval,
            int(self._interval * scale)))
        self._update_stats()
        return self._interval

    @property
    def interval(self) -> int:
        return self._interval


class HistogramScheduler(BaseScheduler):
    """
    Send message either when events reach capacity or when time reaches interval.
    """
    def __init__(self, callback: Callable,
                 capacity: int = 100, interval: int = None,
                 stats_key: str = 'ev_name') -> None:
        super().__init__(callback)

        self._capacity = capacity
        self._interval = interval
        if interval is not None:
            self._interval = interval if interval > 0 else None
        self._stats = HistogramMeter(stats_key)

        self._lock = Lock()
        self._stopped_event = Event()
        self._timeout_event = Event()
        self._cur_time = 0

    def start(self) -> None:
        self._cur_time = time()
        super().start()

    def run(self) -> None:
        timeout = self._interval
        while not self._stopped_event.is_set():
            if not self._timeout_event.wait(timeout):
                self._cur_time = time()
                data = self._stats.get()
                if self._stats.size:  # NOTE: only send non-empty data
                    self._callback(self.route, data)

                now = time()
                timeout = self._cur_time + self._interval - now
                if timeout <= 0:
                    timeout = self._interval
            else:
                self._timeout_event.clear()

    def stop(self) -> None:
        self._timeout_event.set()
        self._stopped_event.set()

    def put(self, value) -> None:
        self._stats.update(value)
        if self._capacity > 0 and self._stats.size >= self._capacity:
            data = self._stats.get()
            self._callback(self.route, data)
            self._timeout_event.set()


class ProxyScheduler(BaseScheduler):
    """
    Direct send message out.
    """
    def __init__(self, callback: Callable) -> None:
        super().__init__(callback)

    def start(self):
        pass

    def stop(self):
        pass

    def put(self, value) -> None:
        self._callback(self.route, value)
