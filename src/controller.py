# Monitor the monitor itself.
# Monitor num queued events, num watches, etc., and send warning message.
# TODO: (Optional) Automatically handle warnings by either
#       - reassign inotify limits, or
#       - add new instances, or
#       - suppress (do not emit) duplicate messages, etc.
# NOTE: inotify limits can be reassigned by
#       `sysctl fs.inotify.max_user_watches=65536`

import macros
import click
import os
import os.path as osp
from datetime import datetime
from threading import Thread, Lock, Event
import sys
from typing import Callable, Final
from loguru import logger
from dispatcher import BaseDispatcher
from tracker import FileTracker
import settings
from event import ExtendedInotifyConstants, ExtendedEvent
from scheduler import EPS, SlidingAverageMeter, IntervalScheduler
from tabulate import tabulate
import utils
import shlex
import pathlib
import glob


class Shell(Thread):
    """A simple shell."""
    def __init__(self, callback: Callable[[str], None]) -> None:
        super().__init__()
        self._callback = callback
        self._stopped_event = Event()
        self._lock = Lock()

    def run(self) -> None:
        while not self._stopped_event.is_set():
            with self._lock:
                cmd = sys.stdin.readline().rstrip('\n')
                self.run_cmd(cmd)
                        
    def run_cmd(self, cmd) -> None:
        if cmd:
            try:
                self._callback(cmd)
            except Exception as e:
                logger.error(repr(e))
                if macros.RAISE_CONTROLLER:
                    raise e

    def query(self, prompt: str) -> str:
        with self._lock:
            ret = input(prompt)
        return ret
    
    def stop(self):
        self._stopped_event.set()


from IPython.terminal.prompts import ClassicPrompts
from IPython.terminal.ipapp import load_default_config
from IPython.terminal.embed import InteractiveShellEmbed
from IPython.core.interactiveshell import ExecutionInfo, ExecutionResult
import sqlite3


class IPythonShell(Shell):
    """A shell based on hacked IPython."""
    def __init__(self, callback: Callable[[str], None], **kwargs) -> None:
        super().__init__(callback)

        config = kwargs.get('config')
        if config is None:
            config = load_default_config()
            config.InteractiveShellEmbed = config.TerminalInteractiveShell
            kwargs['config'] = config
        kwargs['config'].update({'TerminalInteractiveShell':{'colors': 'Linux', 'prompts_class': ClassicPrompts}})

        frame = sys._getframe(1)
        shell = InteractiveShellEmbed.instance(_init_location_id='%s:%s' % (
            frame.f_code.co_filename, frame.f_lineno), **kwargs)
        shell.run_cell = self.run_cmd
        shell.banner1 = ''
        hist = shell.history_manager
        hist.db = sqlite3.connect(hist.hist_file, check_same_thread=False)

        self._shell = shell
        self._frame = frame
    
    def run(self):
        shell = self._shell
        frame = self._frame
        shell(stack_depth=2,_call_location_id='%s:%s' % (frame.f_code.co_filename, frame.f_lineno))
        InteractiveShellEmbed.clear_instance()
        if not self._stopped_event.is_set():
            self._callback('exit')

    def run_cmd(self, cmd, *args, **kwargs) -> None:
        super().run_cmd(cmd)
        info = ExecutionInfo(cmd, True, False, True, None)
        result = ExecutionResult(info)
        result.execution_count = self._shell.execution_count
        self._shell.history_manager.store_inputs(self._shell.execution_count, cmd, cmd)
        self._shell.execution_count += 1
        return result
    
    def stop(self):
        self._stopped_event.set()
        self._shell.ask_exit()


class MasterController:
    OVERFlOW: Final = 'n_overflows'
    READ: Final = 'n_reads'
    EVENT: Final = 'n_events'

    def __init__(self, dispatcher: BaseDispatcher, tracker: FileTracker) -> None:
        self._dispatcher = dispatcher
        self._tracker = tracker
        # By using this flag, we want overflow be instantly but not frequenty notified
        self._warned_overflow = False
        duration = settings.controller_basic_interval
        self._stats = {
            self.OVERFlOW: SlidingAverageMeter(duration),
            self.READ: SlidingAverageMeter(duration),
            self.EVENT: SlidingAverageMeter(duration)
        }
        self._check_scheduler = IntervalScheduler(
            self._warn_limits,
            settings.controller_basic_interval,
            max_interval=settings.controller_max_interval
        )
        self._stats_scheduler = IntervalScheduler(
            self._notify_stats,
            settings.controller_basic_interval,
            max_interval=settings.controller_max_interval,
            stats=self._stats.values()
        )
        self._schedulers = {
            'check': self._check_scheduler,
            'stats': self._stats_scheduler
        }
        self._default_threshold = settings.controller_limit_threshold
        self._thresholds = {}

        self._lock = Lock()
        self._stopped_event = Event()

        self._workers = []

        self._shell = IPythonShell(self.parse_cmd)

    @click.group()
    @click.pass_context
    def _cli(self):
        pass

    @_cli.command('help')
    @click.pass_context
    def __(ctx):
        self = ctx.obj
        logger.info(self._cli.get_help(ctx))

    @_cli.command('exit')
    @click.pass_context
    def __(self):
        self = self.obj
        logger.info('Exiting')
        self.close()
        
    @_cli.command('checkout')
    @click.argument('path', type=click.Path(True, resolve_path=True))
    @click.option('version', '-v', type=int)
    @click.pass_context
    def __(self, path, version):
        self = self.obj
        logger.success(self._tracker.checkout_file(path, version).to_raw())

    @_cli.command('list')
    @click.argument('var', type=click.Choice(['tracker', 'worker']))
    @click.pass_context
    def __(self, var):
        self = self.obj
        if var == 'tracker':
            lst = list(self._tracker)
            logger.success(f'{len(lst)} file(s) being tracked\n' + tabulate(lst, headers='keys'))
        elif var == 'worker':
            dic = {worker.native_id: worker._path_for_wd for worker in self._workers if worker.native_id is not None}
            logger.success(f'{len(dic)} worker(s)\n' + utils.treeify(dic, headers=('worker', 'watch')))

    @_cli.command('clear')
    @click.argument('var', type=click.Choice(['tracker']))
    @click.pass_context
    def __(self, var):
        self = self.obj
        if var == 'tracker':
            cnt = self._tracker.wipe()
            logger.success(f'Removed {cnt} record(s).')

    def _get_worker(self, tid):
        for worker in self._workers:
            if worker.native_id == tid:
                return worker
        logger.error('Worker not found')
            
    @_cli.command('stop')
    @click.option('-t', '--tid', type=int, required=True)
    @click.pass_context
    def __(self, tid):
        self = self.obj
        if worker := self._get_worker(tid):
            worker.stop()
            self._workers.remove(worker)
            logger.success(f'{worker} stopped.')
            
    @_cli.command('recover')
    @click.option('-t', '--tid', type=int)
    @click.pass_context
    def __(self, tid):
        self = self.obj
        if tid is None:
            for worker in self._workers:
                worker.recover()
            logger.success('All workers recovered')
            return
        if worker := self._get_worker(tid):
            worker.recover()
            logger.success(f'{worker} recovered.\n' + utils.treeify(worker._path_for_wd, headers=('watch',)))
            
    @_cli.command('watch')
    @click.argument('paths', type=click.Path(True, resolve_path=True), nargs=-1)
    @click.option('-t', '--tid', type=int, required=True)
    @click.pass_context
    def __(self, paths, tid):
        self = self.obj
        if worker := self._get_worker(tid):
            for path in paths:
                worker._add_dir_watch(os.fsencode(path), worker._mask)
            logger.success(f'Paths added.\n' + utils.treeify(worker._path_for_wd, headers=('watch',)))

    @_cli.command('query')
    @click.option('--from_time', type=click.DateTime())
    @click.option('--to_time', type=click.DateTime())
    @click.option('--pattern')
    @click.option('--mask')
    @click.option('--pid', type=int)
    def __(from_time, to_time, pattern, mask, pid):
        from dispatcher import Route
        from database.conn import SQLEventLogger
        q = SQLEventLogger()
        ret = q.query_event(
            from_time, to_time, pattern,
            Route.parse_mask_from_str(mask) if mask else None, pid
        )
        lst = [{'event': e.full_event_name, 'src': e.src_path, 'dest': e.dest_path, 'time': e._time} for e in ret]
        q.stop()
        logger.success(f'{len(ret)} events\n' + tabulate(lst, headers='keys'))

    def parse_cmd(self, cmd: str) -> None:
        shargs = shlex.split(cmd)  # parse using shell-like syntax
        cmd, shargs = shargs[0], shargs[1:]
        p = pathlib.Path('.')
        args = []
        for a in shargs:
            a = osp.expanduser(a)
            g = glob.glob(a)
            args += g if g else [str(p / a)]
        self._cli.invoke(self._cli.make_context('controller', [cmd] + args, obj=self))

    def start(self):
        for scheduler in self._schedulers.values():
            scheduler.start()
        self._shell.start()

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

        procs = MasterController.get_inotify_procs()
        fields['total_instances'] = sum(len(watches) for watches in procs.values())
        fields['total_watches'] = sum(sum(watches) for watches in procs.values())

        return fields
    
    def _emit(self, msg: str, **kwargs) -> None:
        for route in (ExtendedEvent(ExtendedInotifyConstants.EX_META).
                    select_routes(self._dispatcher.routes)):
            self._dispatcher.emit(route, msg_time=datetime.now(), msg=msg, **kwargs)
    
    def signal_inotify_stats(self, name: str, num: int = 1) -> None:
        Thread(target=self._signal_inotify_stats, args=(name, num)).start()

    def _signal_inotify_stats(self, name: str, num: int = 1) -> None:
        with self._lock:
            if name not in self._stats:
                raise KeyError(f"Unknown key {name}")
            self._stats[name].update(num)
            if name == self.OVERFlOW and not self._warned_overflow:
                self._emit(
                    'Inotify overflow occurred',
                    msg_zh=
                    '发生事件队列溢出'
                )
                self._warned_overflow = True  # only warn the first one of consecutive overflows

    def _warn_limits(self) -> float:
        priority = 5  # if no messages, increase checking frequency

        info = self.get_inotify_info()
        instance_used = info['total_instances'] / info['max_user_instances']
        watch_used = info['total_watches'] / info['max_user_watches']
        if instance_used > self._default_threshold or watch_used > self._default_threshold:
            self._emit(
                f'Used instances: {info["total_instances"]} / {info["max_user_instances"]} '
                f'({instance_used*100:.2f}%)\n'
                f'Used watches: {info["total_watches"]} / {info["max_user_watches"]} '
                f'({watch_used*100:.2f}%)',
                msg_zh=
                f'已用 instance 数: {info["total_instances"]} / {info["max_user_instances"]} '
                f'({instance_used*100:.2f}%)\n'
                f'已用 watch 数: {info["total_watches"]} / {info["max_user_watches"]} '
                f'({watch_used*100:.2f}%)'
            )
            priority = -1  # lower the priority since we have already sent messages

        n_workers = len(self._workers)
        n_inactive_workers = len([worker for worker in self._workers if not worker.is_alive()])
        if n_inactive_workers:
            self._emit(
                f'Workers down: {n_inactive_workers} / {n_workers}',
                msg_zh=
                f'Worker 崩溃: {n_inactive_workers} / {n_workers}'
            )
            priority = -1

        return priority
        
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
                f'{sums[self.OVERFlOW][1]} overflows',
                msg_zh=
                f'在 {self._stats[self.OVERFlOW].duration} 秒内: '
                f'读事件 {sums[self.READ][1]} 次, '
                f'读出事件 {sums[self.EVENT][1]} 个, '
                f'发生溢出 {sums[self.OVERFlOW][1]} 次'
            )
        else:
            # We may warn overflow again later as we have not seen it for it while
            self._warned_overflow = False
        if ope > prev_ope:
            return 1  # the more overflow events, the higher priority
        else:
            return -1
        
    def add_worker(self, worker) -> None:
        self._workers.append(worker)
    
    def close(self) -> None:
        if self._stopped_event.is_set():
            return
        self._stopped_event.set()

        for worker in self._workers:
            worker.stop()
        for scheduler in self._schedulers.values():
            scheduler.stop()
        self._shell.stop()
