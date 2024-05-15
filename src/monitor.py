import ctypes
import struct
import threading
import errno
import os
import os.path as osp
from queue import Queue
from typing import Iterable, List
from loguru import logger
from database.conn import SQLEventLogger
from linux import *
from tracker import FileTracker
from dispatcher import BaseDispatcher, Dispatcher
from controller import MonitorController
from event import InotifyEvent
from buffer import InotifyBuffer
import settings


EVENT_SIZE = ctypes.sizeof(inotify_event_struct)
DEFAULT_NUM_EVENTS = 2048
DEFAULT_EVENT_BUFFER_SIZE = DEFAULT_NUM_EVENTS * (EVENT_SIZE + 16)


class Worker(threading.Thread):
    def __init__(self, paths: List[str], channel: BaseDispatcher,
                 controller: MonitorController, watch_link: bool = True):
        super().__init__()
        self._stopped_event = threading.Event()
        self._channel = channel
        self._controller = controller
        self._controller.add_worker(self)

        self._lock = threading.Lock()
        self._fd = inotify_init()
        logger.debug(f'pid: {os.getpid()}')
        logger.debug(f'fd: {self._fd}')
        self._wd_for_path = {}
        self._path_for_wd = {}
        # These are used for watching symbolic links
        self._links_for_path = {}  # target -> list of links
        self._path_for_link = {}  # link -> unique target

        self._watch_link = watch_link
        self._db_logger = SQLEventLogger()
        self._db_logger.init_conn()

        self._callback_queue = Queue()

        for path in paths:
            self._add_dir_watch(os.fsencode(path))

    def start(self):
        super().start()

    @staticmethod
    def _parse_event_buffer(event_buffer):
        i = 0
        while i + 16 <= len(event_buffer):
            wd, mask, cookie, length = struct.unpack_from("iIII", event_buffer, i)
            name = event_buffer[i + 16 : i + 16 + length].rstrip(b"\0")
            i += 16 + length
            yield wd, mask, cookie, name

    def _read_events(self, event_buffer_size=DEFAULT_EVENT_BUFFER_SIZE):
        event_buffer = None
        while True:
            try:
                event_buffer = os.read(self._fd, event_buffer_size)
            except OSError as e:
                if e.errno == errno.EINTR:
                    continue
                elif e.errno == errno.EBADF:
                    return []
                else:
                    raise
            break
        self._controller.signal_inotify_stats(self._controller.READ)

        event_list = []
        for wd, mask, cookie, name in self._parse_event_buffer(event_buffer):
            if mask & InotifyConstants.IN_Q_OVERFLOW:
                self._controller.signal_inotify_stats(self._controller.OVERFlOW)
                # NOTE: The entire queue is dropped when an overflow occurs
                # NOTE: Overflow can be triggered by list(os.walk(link_loop, followlinks=True))
                logger.critical('Queue overflow occurred')
                event_list.append(InotifyEvent(wd, mask, cookie, name, b''))
                continue
            if mask & InotifyConstants.IN_IGNORED:
                logger.warning('Event ignored')
                event_list.append(InotifyEvent(wd, mask, cookie, name, b''))
                continue

            wd_path = self._path_for_wd[wd]
            src_path = osp.join(wd_path, name) if name else wd_path  # avoid trailing slash
            event = InotifyEvent(wd, mask, cookie, name, src_path)
            event_list.append(event)

            src_path_d = os.fsdecode(src_path)
            if event.is_create_file:
                if osp.islink(src_path):
                    self._add_link_watch(src_path)
                elif osp.isfile(src_path):
                    self._controller._tracker.watch_or_compare(src_path_d, self._queue_for_emit)
            elif event.is_modify_file:
                if osp.islink(src_path):  # e.g., ln -sfn
                    self._rm_link_watch(src_path)
                    self._add_link_watch(src_path)
                elif osp.isfile(src_path):
                    # event.select_procs()
                    # logger.success(event._proc)
                    self._controller._tracker.watch_or_compare(src_path_d, self._queue_for_emit)
            elif event.is_delete_file:
                if src_path in self._path_for_link:
                    self._rm_link_watch(src_path)
                # NOTE: We do not record the deletion of a tracked file, and when
                #       the file is created again, it is regarded as the previous one.
            
            if event.is_create_dir:
                self._add_dir_watch(src_path)
            
            elif event.is_delete_dir:
                self._rm_watch(wd)

        self._controller.signal_inotify_stats(self._controller.EVENT, len(event_list))
        return event_list
    
    def _add_link_watch(self, src_path, mask=InotifyConstants.IN_ALL_EVENTS):
        """
        Note
        ----
        If a symbolic link is created before the target,
        the link is ignored and the target will not be watched.
        For example,
        ```
        ln -s bb a
        mkdir bb
        ```
        """
        if not self._watch_link:
            return
        
        path = src_path
        while osp.islink(path):
            path = osp.abspath(osp.join(osp.split(path)[0], os.readlink(path)))
            break  # TODO: recursively follow a link and detect possible loops
        dest_path = path
        if osp.islink(dest_path) or not osp.isdir(dest_path):
            return  # TODO: consider linking a file
        
        if dest_path not in self._links_for_path:
            self._links_for_path[dest_path] = set()
            # Add a dummy link marked as None if dest was being watched
            # so that it will still be watched when the real links are removed
            if dest_path in self._wd_for_path:
                self._links_for_path[dest_path].add(None)
            else:
                self._add_dir_watch(dest_path, mask)
        self._links_for_path[dest_path].add(src_path)
        self._path_for_link[src_path] = dest_path
        logger.debug(f'links: {self._links_for_path}')

    def _rm_link_watch(self, link):
        path = self._path_for_link[link]
        del self._path_for_link[link]
        self._links_for_path[path].remove(link)
        if not self._links_for_path[path]:
            del self._links_for_path[path]
            self._rm_dir_watch(self._wd_for_path[path])
        logger.debug(f'links: {self._links_for_path}')
    
    def _add_dir_watch(self, path, mask=InotifyConstants.IN_ALL_EVENTS):
        if not osp.isdir(path):
            raise OSError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), path)
        self._add_watch(path, mask)

        for root, dirnames, _ in os.walk(path):
            for dirname in dirnames:
                full_path = osp.join(root, dirname)
                if not osp.islink(full_path):
                    self._add_watch(full_path, mask)

        for root, dirnames, filenames in os.walk(path):
            for dirname in dirnames:
                full_path = osp.join(root, dirname)
                if osp.islink(full_path):
                    self._add_link_watch(full_path, mask)
            for filename in filenames:
                full_path = osp.join(root, filename)
                if osp.islink(full_path):
                    self._add_link_watch(full_path, mask)

    def _rm_dir_watch(self, wd):
        path = self._path_for_wd[wd]
        self._rm_watch(wd)
        
        for root, dirnames, _ in os.walk(path):
            for dirname in dirnames:
                full_path = osp.join(root, dirname)
                if not osp.islink(full_path):
                    self._rm_watch(self._wd_for_path[full_path])
                # TODO: Remove links in the dir
        
    def _add_watch(self, path, mask=InotifyConstants.IN_ALL_EVENTS):
        # int inotify_add_watch(int fd, const char *pathname, uint32_t mask);
        wd = inotify_add_watch(self._fd, path, mask)
        assert wd != -1
        self._wd_for_path[path] = wd
        self._path_for_wd[wd] = path
        logger.debug(f'wd: {self._path_for_wd}')

    def _rm_watch(self, wd):
        inotify_rm_watch(self._fd, wd)
        path = self._path_for_wd[wd]
        del self._wd_for_path[path]
        del self._path_for_wd[wd]
        logger.debug(f'wd: {self._path_for_wd}')

    def _queue_for_emit(self, event):
        self._callback_queue.put(event)

    def _emit(self, event):
        for route in event.select_routes(self._channel.routes):
            self._channel.emit(self._channel.gen_data_msg(
                tag=route.tag, msg=route.format.format(**event.get_fields())))

    def run(self):
        while not self._stopped_event.is_set():
            for event in InotifyBuffer._group_event(self._read_events()):
                self._emit(event)
                self._db_logger.log_event(event)
            # FIXME: _read_events() blocks _callback_queue
            while not self._callback_queue.empty():
                event = self._callback_queue.get()
                self._emit(event)
                self._db_logger.log_event(event)

    def stop(self):
        self._db_logger.stop()
        self._stopped_event.set()


def main(args):
    dispatcher = Dispatcher()
    tracker = FileTracker()
    for path in args.paths:
        tracker.watch_dir(path)
    controller = MonitorController(dispatcher, tracker)
    logger.info(f'Monitoring {args.paths}.')

    workers = []
    if settings.worker_every_path:
        for path in args.paths:
            workers.append(Worker([path], dispatcher, controller))
    else:
        workers = [Worker(args.paths, dispatcher, controller)]
    
    for worker in workers:
        worker.start()

    for worker in workers:
        worker.join()
    dispatcher.close()


if __name__ == '__main__':
    import argparse
    from collections.abc import Iterable
    import json

    parser = argparse.ArgumentParser()
    parser.add_argument('paths', type=str, nargs='+')
    parser.add_argument('--config_files', type=str, nargs='*', default=[])

    # Add settings.* to exec options
    items = {}
    for item in dir(settings):
        if not item.startswith('_'):
            value = getattr(settings, item)
            if isinstance(value, dict):
                continue  # can only by modified by json config files
            elif isinstance(value, Iterable) and not isinstance(value, str):
                parser.add_argument(
                    f'--{item}',
                    type=value.dtype if hasattr(value, 'dtype') else type(value[0]),
                    nargs='*', default=None)
            elif isinstance(value, bool):
                if value:
                    parser.add_argument(f'--{item}', action='store_false')
                else:
                    parser.add_argument(f'--{item}', action='store_true')
            else:
                parser.add_argument(f'--{item}', type=type(value), default=None)
            items[item] = value

    args = parser.parse_args()

    for config_file in args.config_files:
        logger.info(f'{config_file} > settings')
        with open(config_file, 'r') as fi:
            cfg = json.load(fi)
        for item in items:
            if item in cfg:
                value = type(items[item])(cfg[item])
                setattr(settings, item, value)
                logger.info(f'settings.{item} = {value}')

    logger.info(f'arguments > settings')
    for item in items:
        value = getattr(args, item)
        if value is not None and value != items[item]:
            setattr(settings, item, value)
            logger.info(f'settings.{item} = {value}')

    main(args)
