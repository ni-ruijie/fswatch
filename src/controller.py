# Monitor the monitor itself.
# Monitor num queued events, num watches, etc., and send warning message.
# TODO: (Optional) Automatically handle warnings by either
#       - reassign inotify limits, or
#       - add new instances, or
#       - suppress (do not emit) duplicate messages, etc.
# NOTE: inotify limits can be reassigned by
#       `sysctl fs.inotify.max_user_watches=65536`

import argparse
import os
import os.path as osp
from collections import deque
from time import time
from threading import Thread, Lock, Event
import sys
from typing import Iterable, Callable, Final, Union
from loguru import logger
from dispatcher import BaseDispatcher
from tracker import FileTracker
import settings


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
    

class MovingAverageMeter(BaseMeter):
    pass


class IntervalScheduler(Thread):
    """
    Schedules the frequency of messages dynamically.

    Parameters
    ----------
    callback: function
        Called every interval. Returns a priority value ranged from [-1, 1].
        A negative priority increase the interval while a positive one does
        the opposite.

    init_interval: int
        The initial interval duration.
    """
    def __init__(self, callback: Callable[[], float], init_interval: int,
                 min_interval: int = None, max_interval: int = None,
                 stats: Iterable[SlidingAverageMeter] = None) -> None:
        super().__init__()

        self._callback = callback
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
                priority = self._callback()
                if priority < 0:
                    self.increase()
                elif priority > 0:
                    self.decrease()
                if priority:
                    logger.debug(f'{self} Priority {priority} Interval {self._interval}')

                now = time()
                timeout = self._cur_time + self._interval - now
                # FIXME: # Only in case callback takes more than an interval to complete
                if timeout <= 0:
                    timeout = self._min_interval

    def stop(self) -> None:
        self._stopped_event.set()

    def _update_stats(self) -> None:
        for stat in self._stats:
            stat.reset_duration(self._interval)

    def increase(self) -> int:
        self._interval = min(self._max_interval, self._interval * 2)
        self._update_stats()
        return self._interval

    def decrease(self) -> int:
        self._interval = max(self._min_interval, self._interval // 2)
        self._update_stats()
        return self._interval

    @property
    def interval(self) -> int:
        return self._interval


class Shell(Thread):
    def __init__(self, callback: Callable[[str], None]) -> None:
        super().__init__()
        self._callback = callback
        self._stopped_event = Event()
        self._lock = Lock()

    def run(self) -> None:
        while not self._stopped_event.is_set():
            with self._lock:
                cmd = sys.stdin.readline().rstrip('\n')
                if cmd:
                    try:
                        self._callback(cmd)
                    except Exception as e:
                        logger.error(e)

    def query(self, prompt: str) -> str:
        with self._lock:
            ret = input(prompt)
        return ret


class MonitorController:
    OVERFlOW: Final = 'n_overflows'
    READ: Final = 'n_reads'
    EVENT: Final = 'n_events'

    def __init__(self, dispatcher: BaseDispatcher, tracker: FileTracker) -> None:
        self._dispatcher = dispatcher
        self._tracker = tracker
        # By using this flag, we want overflow be instantly but not frequenty notified
        self._warned_overflow = False
        duration = settings.basic_controller_interval
        self._stats = {
            self.OVERFlOW: SlidingAverageMeter(duration),
            self.READ: SlidingAverageMeter(duration),
            self.EVENT: SlidingAverageMeter(duration)
        }
        self._check_scheduler = IntervalScheduler(
            self._warn_limits, duration, max_interval=duration*24)
        self._stats_scheduler = IntervalScheduler(
            self._notify_stats, duration, duration//6, duration*24, stats=self._stats.values())
        self._schedulers = {
            'check': self._check_scheduler,
            'stats': self._stats_scheduler
        }
        self._default_threshold = settings.controller_limit_threshold
        self._thresholds = {}

        for scheduler in self._schedulers.values():
            scheduler.start()

        self._shell = Shell(self.parse_cmd)
        self._shell.start()

    def parse_cmd(self, cmd: str) -> None:
        name = cmd.split()[0]
        if name == 'checkout':
            parser = argparse.ArgumentParser()
            parser.add_argument('path', type=str)
            parser.add_argument('version', type=int)
            args = parser.parse_args(cmd.split()[1:])
            logger.success(self._tracker.checkout_file(args.path, args.version))
        elif name == 'list_tracked':
            logger.success(list(self._tracker._fid_for_path.keys()))
        else:
            logger.error(f'Command not recognized: {cmd}')

    @staticmethod
    def get_inotify_procs() -> dict:
        pids = [x for x in os.listdir('/proc') if x.isdigit()]
        procs = {}
        for pid in pids:
            watches = []
            try:
                fds = os.listdir(f'/proc/{pid}/fd')
            except (PermissionError, FileNotFoundError):
                continue
            for fd in fds:
                try:
                    name = os.readlink(f'/proc/{pid}/fd/{fd}')
                except (PermissionError, FileNotFoundError):
                    continue
                if name == 'anon_inode:inotify' or name == 'inotify':
                    watch = 0
                    # pos:    
                    # flags:  
                    # mnt_id: 
                    # inotify wd: ino: ...
                    try:
                        with open(f'/proc/{pid}/fdinfo/{fd}', 'r') as fi:
                            for line in fi.readlines():
                                if line.startswith('inotify wd:'):
                                    watch += 1
                    except (PermissionError, FileNotFoundError):
                        continue
                    watches.append(watch)
            if watches:
                procs[pid] = watches
        return procs

    @staticmethod
    def get_inotify_info() -> dict:
        fields = {field: None for field in ('max_queued_events', 'max_user_instances', 'max_user_watches')}
        for field in fields:
            with open(osp.join('/proc/sys/fs/inotify', field), 'r') as fi:
                fields[field] = int(fi.read())

        procs = MonitorController.get_inotify_procs()
        fields['total_instances'] = sum(len(watches) for watches in procs.values())
        fields['total_watches'] = sum(sum(watches) for watches in procs.values())

        return fields
    
    def signal_inotify_stats(self, name: str, num: int = 1) -> None:
        if name not in self._stats:
            raise KeyError(f"Unknown key {name}")
        self._stats[name].update(num)
        if name == self.OVERFlOW and not self._warned_overflow:
            self._dispatcher.emit(self._dispatcher.gen_data_msg(
                msg='Inotify overflow occurred'))
            self._warned_overflow = True  # TODO: unset this flag sometime later

    def _warn_limits(self) -> float:
        info = self.get_inotify_info()
        instance_used = info['total_instances'] / info['max_user_instances']
        watch_used = info['total_watches'] / info['max_user_watches']
        if instance_used > self._default_threshold or watch_used > self._default_threshold:
            self._dispatcher.emit(self._dispatcher.gen_data_msg(
                msg=f'Used instances: {info["total_instances"]} / {info["max_user_instances"]} '
                f'({instance_used*100:.2f}%)\n'
                f'Used watches: {info["total_watches"]} / {info["max_user_watches"]} '
                f'({watch_used*100:.2f}%)'))
            return -1  # lower the priority since we have already sent messages
        return 1
        
    def _notify_stats(self) -> float:
        sums = {stat: (self._stats[stat].get_prev()['sum'],
                       self._stats[stat].get()['sum']) for stat in self._stats}
        prev_ope, ope = [sums[self.OVERFlOW][i] / (sums[self.EVENT][i] + EPS) \
                         for i in range(2)]  # overflow per event
        if sums[self.OVERFlOW][1]:
            self._dispatcher.emit(self._dispatcher.gen_data_msg(
                msg=f'Over past {self._stats[self.OVERFlOW].duration} secs: '
                f'{sums[self.READ][1]} reads, '
                f'{sums[self.EVENT][1]} events, '
                f'{sums[self.OVERFlOW][1]} overflows'))
        if ope > prev_ope:
            return 1  # the more overflow events, the higher priority
        else:
            return -1
    
    def close(self) -> None:
        for scheduler in self._schedulers.values():
            scheduler.stop()
        self._shell.stop()
