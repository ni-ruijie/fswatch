# Monitor the monitor itself.
# Monitor num queued events, num watches, etc., and send warning message.
# TODO: (Optional) Automatically handle warnings by either
#       - reassign inotify limits, or
#       - add new instances, or
#       - suppress (do not emit) duplicate messages, etc.
# NOTE: inotify limits can be reassigned by
#       `sysctl fs.inotify.max_user_watches=65536`

import os
import os.path as osp
from collections import deque
from time import time
from typing import Iterable
from dispatcher import BaseDispatcher


class BaseMeter:
    def __init__(self) -> None:
        self._prev = None

    def update(self, value: int | float) -> None:
        pass

    def get_prev(self) -> dict:
        return self._prev

    def get(self) -> dict:
        pass
    

class SlidingAvereageMeter(BaseMeter):
    def __init__(self, duration: int | float) -> None:
        super().__init__()
        self._queue = deque()
        self._duration = duration
        self._prev = None

    @property
    def duration(self):
        return self._duration

    def reset_duration(self, duration: int | float) -> None:
        if duration > self._duration:
            pass  # TODO: increase duration by time
        self._duration = duration

    def update(self, value: int | float) -> None:
        now = time()
        self._queue.append((now, value))
        while self._queue[0][0] <= now - self._duration:
            self._queue.popleft()

    def get_prev(self) -> dict:
        return self._prev

    def get(self) -> dict:
        tot = sum(x[1] for x in self._queue)
        avg = tot / len(self._queue)
        self._prev = {'sum': tot, 'avg': avg}
        return self._prev
    

class MovingAverageMeter(BaseMeter):
    pass


class IntervalScheduler:
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
    def __init__(self, callback: function, init_interval: int,
                 min_interval: int = None, max_interval: int = None,
                 stats: Iterable = None) -> None:
        self._callback = callback
        self._interval = init_interval
        self._min_interval = init_interval if min_interval is None else min_interval
        self._max_interval = init_interval if max_interval is None else max_interval
        self._stats = stats or []

    def start(self) -> None:
        # TODO: Add timer
        pass

    def _update_stats(self) -> None:
        for stat in self.stats:
            stat.reset_duration(self._interval)

    def increase(self) -> int:
        self._interval = max(self._min_interval, self._interval // 2)
        self._update_stats()
        return self._interval

    def decrease(self) -> int:
        self._interval = min(self._max_interval, self._interval * 2)
        self._update_stats()
        return self._interval

    @property
    def interval(self) -> int:
        return self._interval


class MonitorController:
    def __init__(self, dispatcher: BaseDispatcher) -> None:
        self._dispatcher = dispatcher
        # By using this flag, we want overflow be instantly but not frequenty notified
        self._warned_overflow = False
        self._check_scheduler = IntervalScheduler(
            self._warn_limits, 60*60, max_interval=24*60*60)
        self._stats_scheduler = IntervalScheduler(
            self._notify_stats, 60*60, 10*60, 24*60*60)
        self._stats = {
            'n_overflows': SlidingAvereageMeter(self._duration),
            'n_reads': SlidingAvereageMeter(self._duration),
            'n_events': SlidingAvereageMeter(self._duration)
        }
        self._default_threshold = 0.9
        self._thresholds = {}

        self._check_scheduler.start()
        self._stats_scheduler.start()

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
    
    def signal_inotify_overflow(self, num: int = 1) -> None:
        self._stats['n_overflows'].update(num)
        if not self._warned_overflow:
            self._dispatcher.emit(self._dispatcher.gen_data_msg(
                msg='Inotify overflow occurred'))
            self._warned_overflow = True  # TODO: unset this flag sometime later

    def _warn_limits(self) -> float:
        info = self.get_inotify_info()
        instance_used = info['total_instances'] / info['max_user_instances']
        watch_used = info['total_watches'] / info['max_user_watches']
        if instance_used > self._default_threshold or watch_used > self._default_threshold:
            self._dispatcher.emit(self._dispatcher.gen_data_msg(
                msg=f'Used instances: {info['total_instances']} / {info['max_user_instances']}'
                f'({instance_used*100:.2f}%)\n'
                f'Used watches: {info['total_watches']} / {info['max_user_watches']}'
                f'({watch_used*100:.2f}%)'))
            return -1
        return 1
        
    def _notify_stats(self) -> float:
        sums = {stat: (self._stats[stat].get_prev()['sum'],
                       self._stats[stat].get()['sum']) for stat in self._stats}
        prev_ope, ope = [sums['n_overflows'][i] / sums['n_events'][i] for i in range(2)]  # overflow per event
        if sums['n_overflows'][1]:
            self._dispatcher.emit(self._dispatcher.gen_data_msg(
                msg=f'Over past {self._stats['n_overflows'].duration} secs: '
                f'{sums['n_reads'][1]} reads, '
                f'{sums['n_events'][1]} events, '
                f'{sums['n_overflow'][1]} overflows'))
        if ope > prev_ope:
            return 1
        elif ope < prev_ope:
            return -1
        return 0
    