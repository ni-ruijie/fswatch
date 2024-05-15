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
from time import time
from threading import Thread, Lock, Event
import sys
from typing import Callable, Final
from loguru import logger
from dispatcher import BaseDispatcher
from tracker import FileTracker
import settings
from event import ExtendedInotifyConstants, ExtendedEvent
from scheduler import EPS, SlidingAverageMeter, IntervalScheduler


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
    
    def stop(self):
        self._stopped_event.set()


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

        self._lock = Lock()

        self._workers = []

        for scheduler in self._schedulers.values():
            scheduler.start()

        self._shell = Shell(self.parse_cmd)
        self._shell.start()

    def parse_cmd(self, cmd: str) -> None:
        name = cmd.split()[0]
        if name == 'exit':
            logger.info('Exiting')
            self.close()
        elif name == 'checkout':
            parser = argparse.ArgumentParser()
            parser.add_argument('path', type=str)
            parser.add_argument('version', type=int)
            args = parser.parse_args(cmd.split()[1:])
            logger.success(self._tracker.checkout_file(args.path, args.version))
        elif name == 'list_tracked':
            logger.success(list(self._tracker))
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
    
    def _emit(self, msg: str) -> None:
        for route in (ExtendedEvent(ExtendedInotifyConstants.EX_META).
                    select_routes(self._dispatcher.routes)):
            self._dispatcher.emit(route, msg=msg)
    
    def signal_inotify_stats(self, name: str, num: int = 1) -> None:
        Thread(target=self._signal_inotify_stats, args=(name, num)).start()

    def _signal_inotify_stats(self, name: str, num: int = 1) -> None:
        with self._lock:
            if name not in self._stats:
                raise KeyError(f"Unknown key {name}")
            self._stats[name].update(num)
            if name == self.OVERFlOW and not self._warned_overflow:
                self._emit('Inotify overflow occurred')
                self._warned_overflow = True  # TODO: unset this flag sometime later

    def _warn_limits(self) -> float:
        info = self.get_inotify_info()
        instance_used = info['total_instances'] / info['max_user_instances']
        watch_used = info['total_watches'] / info['max_user_watches']
        if instance_used > self._default_threshold or watch_used > self._default_threshold:
            self._emit(
                f'Used instances: {info["total_instances"]} / {info["max_user_instances"]} '
                f'({instance_used*100:.2f}%)\n'
                f'Used watches: {info["total_watches"]} / {info["max_user_watches"]} '
                f'({watch_used*100:.2f}%)'
            )
            return -1  # lower the priority since we have already sent messages
        return 1
        
    def _notify_stats(self) -> float:
        sums = {stat: (self._stats[stat].get_prev()['sum'],
                       self._stats[stat].get()['sum']) for stat in self._stats}
        prev_ope, ope = [sums[self.OVERFlOW][i] / (sums[self.EVENT][i] + EPS) \
                         for i in range(2)]  # overflow per event
        if sums[self.OVERFlOW][1]:
            self._emit(
                f'Over past {self._stats[self.OVERFlOW].duration} secs: '
                f'{sums[self.READ][1]} reads, '
                f'{sums[self.EVENT][1]} events, '
                f'{sums[self.OVERFlOW][1]} overflows'
            )
        if ope > prev_ope:
            return 1  # the more overflow events, the higher priority
        else:
            return -1
        
    def add_worker(self, worker) -> None:
        self._workers.append(worker)
    
    def close(self) -> None:
        for worker in self._workers:
            worker.stop()
        for scheduler in self._schedulers.values():
            scheduler.stop()
        self._shell.stop()
