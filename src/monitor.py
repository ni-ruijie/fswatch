#!/usr/bin/python3

import ctypes
import struct
import threading
import errno
import os
import os.path as osp
import select
import sys
from queue import Queue
from typing import Iterable, List
from loguru import logger
from database.conn import SQLEventLogger
from linux import *
from tracker import FileTracker
from dispatcher import BaseDispatcher, Dispatcher, Route
from controller import MasterController
from event import *
from buffer import InotifyBuffer
import settings


EVENT_SIZE = ctypes.sizeof(inotify_event_struct)
DEFAULT_NUM_EVENTS = 2048
DEFAULT_EVENT_BUFFER_SIZE = DEFAULT_NUM_EVENTS * (EVENT_SIZE + 16)


class Worker(threading.Thread):
    def __init__(self, paths: List[str], channel: BaseDispatcher,
                 controller: MasterController, watch_link: bool = True,
                 mask: int = InotifyConstants.IN_ALL_EVENTS):
        super().__init__()
        self._stopped_event = threading.Event()
        self._crashed = False
        self._channel = channel
        self._controller = controller

        self._lock = threading.Lock()
        self._signal_r, self._signal_w = os.pipe()

        self._fd = inotify_init()
        self._blocking = settings.worker_blocking_read
        os.set_blocking(self._fd, self._blocking)
        self._wd_for_path = {}
        self._path_for_wd = {}
        # These are used for watching symbolic links
        self._links_for_path = {}  # target -> list of links
        self._path_for_link = {}  # link -> unique target

        self._watch_link = watch_link
        self._mask = mask
        self._db_logger = SQLEventLogger()
        self._db_logger.init_conn()

        self._buffer = InotifyBuffer(self._read_events)

        self._init_paths = paths
        logger.debug(f'Worker {self}: Watching {paths} using Inotify instance {self._fd}')
        for path in paths:
            self._add_dir_watch(os.fsencode(path), self._mask)

    def start(self):
        self._buffer.start()
        super().start()
        self._controller.add_worker(self)
        self._db_logger.start()

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
        if self._blocking:
            while True:
                try:
                    event_buffer = os.read(self._fd, event_buffer_size)
                except OSError as e:
                    if e.errno == errno.EINTR:
                        continue
                    elif e.errno == errno.EBADF:
                        return []
                    else:
                        self._raise(e)
                break
        else:
            rlist, _, _ = select.select([self._fd, self._signal_r], [], [])
            if self._fd not in rlist:
                return []
            try:
                event_buffer = os.read(self._fd, event_buffer_size)
            except OSError as e:
                if e.errno == errno.EBADF:
                    return []
                else:
                    self._raise(e)

        self._controller.signal_inotify_stats(self._controller.READ)

        event_list = []
        for wd, mask, cookie, name in self._parse_event_buffer(event_buffer):
            if mask & InotifyConstants.IN_Q_OVERFLOW:
                self._controller.signal_inotify_stats(self._controller.OVERFlOW)
                # NOTE: The entire queue is dropped when an overflow occurs
                # NOTE: Overflow can be triggered by list(os.walk(link_loop, followlinks=True))
                logger.critical('Queue overflow occurred')
                event_list.append(InotifyEvent(wd, mask, cookie, name, b''))
                # Do _add_dir_watch after overflow since IN_ISDIR|IN_CREATE events may be dropped
                # XXX: Better in separated thread to prevent another overflow?
                self.recover()
                logger.success(f'Auto-recover watches after overflow.')
                continue
            if mask & InotifyConstants.IN_IGNORED:
                # logger.warning('Event ignored')
                event_list.append(InotifyEvent(wd, mask, cookie, name, b''))
                continue

            if not wd in self._path_for_wd:
                continue  # AUTO_RECOVERY: wd may have been removed
            wd_path = self._path_for_wd[wd]
            src_path = osp.join(wd_path, name) if name else wd_path  # avoid trailing slash
            event = InotifyEvent(wd, mask, cookie, name, src_path)
            event_list.append(event)

            if event.is_create_link:
                self._add_link_watch(src_path, self._mask)
            elif event.is_modify_link:  # e.g., ln -sfn
                self._rm_link_watch(src_path)
                self._add_link_watch(src_path, self._mask)
            elif event.is_delete_file and src_path in self._path_for_link:
                self._rm_link_watch(src_path)
            
            if event.is_create_dir:
                self._add_dir_watch(src_path, self._mask)
            
            elif event.is_delete_dir:
                self._rm_watch(wd)

        self._controller.signal_inotify_stats(self._controller.EVENT, len(event_list))
        return event_list
    
    def recover(self):
        self._clean_watch()
        for path in self._init_paths:
            path = os.fsencode(path)
            self._add_dir_watch(path, self._mask)
    
    def _add_link_watch(self, src_path, mask):
        """
        Note
        ----
        If a symbolic link is created before the target,
        the dangling link is ignored and the target will not be watched.
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
    
    def _add_dir_watch(self, path, mask):
        if not osp.isdir(path):
            logger.warning(f'{path} is not a directory')
            # raise OSError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), path)
            return
        ret = self._add_watch(path, mask)
        if not ret:
            return

        # Add subdirs
        for root, dirnames, _ in os.walk(path):
            for dirname in dirnames:
                full_path = osp.join(root, dirname)
                if not osp.islink(full_path):
                    self._add_watch(full_path, mask)

        # Add links
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
                    if not full_path in self._wd_for_path:
                        continue  # AUTO_RECOVERY:
                    self._rm_watch(self._wd_for_path[full_path])
                # TODO: Remove links in the dir

    def _clean_watch(self):
        for wd in list(self._path_for_wd):
            self._rm_watch(wd)
        
    def _add_watch(self, path, mask):
        if path in self._wd_for_path:
            return  # AUTO_RECOVERY:
        # int inotify_add_watch(int fd, const char *pathname, uint32_t mask);
        wd = inotify_add_watch(self._fd, path, mask)
        if wd == -1:
            err = ctypes.get_errno()
            if err == errno.ENOENT:
                logger.warning(f'{path} does not exist')
                return
            elif err == errno.ENOTDIR:
                logger.warning(f'{path} is not a directory')
                return
            elif err == errno.EEXIST:
                logger.warning(f'{path} has already been watched')
                return
            elif err == errno.EACCES:
                logger.warning(f'{path} permission denied')
                # TODO: Remove watch
                return
            self._raise(SystemError(
                f'inotify_add_watch failed with unexpected errno {errno.errorcode[err]} '
                '(check https://www.man7.org/linux/man-pages/man2/inotify_add_watch.2.html#ERRORS for details)'))
        elif wd in self._path_for_wd:  # Changed
            pass
        self._wd_for_path[path] = wd
        self._path_for_wd[wd] = path
        # logger.debug(f'wd: {self._path_for_wd}')
        return True

    def _rm_watch(self, wd):
        inotify_rm_watch(self._fd, wd)
        path = self._path_for_wd[wd]
        del self._wd_for_path[path]
        del self._path_for_wd[wd]
        # logger.debug(f'wd: {self._path_for_wd}')

    def _emit(self, event):
        for route in event.select_routes(self._channel.routes):
            self._channel.emit(route, **event.get_fields())

    def run(self):
        while not self._stopped_event.is_set():
            event = self._buffer.read_event()
            if event is not None:
                self._emit(event)
                self._db_logger.log_event(event)

                if event.is_create_file or event.is_modify_file:
                    self._controller._tracker.watch_or_compare(event.src_path, self._buffer._queue.put)
                elif event.is_moveto_file:
                    # Watch dest `b` if `mv a b`; or watch src `b` if `mv ../a b`
                    self._controller._tracker.watch_or_compare(
                        event.dest_path or event.src_path, self._buffer._queue.put)
                # NOTE: We do not record the deletion of a tracked file, and when
                #       the file is created again, it is regarded as the previous one.

    def stop(self):
        if self._stopped_event.is_set():
            return
        self._db_logger.stop()
        self._stopped_event.set()
        self._buffer.stop()

        os.write(self._signal_w, b' ')
        os.close(self._fd)
        self._wd_for_path = {}
        self._path_for_wd = {}
        self._links_for_path = {}
        self._path_for_link = {}

    def _raise(self, e):
        self._crashed = True
        self.stop()
        raise(e)

    @property
    def is_crashed(self):
        return not self.is_alive() and (self._crashed or not self._stopped_event.is_set())

    def __str__(self):
        return f'<{self.__class__.__name__}(Thread-{self.native_id}, {self._init_paths})>'


def main(args):
    # ===  Init  ===

    dispatcher = Dispatcher(name=args.name)
    mask = InotifyConstants.IN_CREATE | InotifyConstants.IN_DELETE_SELF | InotifyConstants.IN_MODIFY \
         | InotifyConstants.IN_MOVED_FROM | InotifyConstants.IN_MOVED_TO | InotifyConstants.IN_MOVE_SELF \
         | InotifyConstants.IN_ATTRIB \
         | InotifyConstants.IN_ONLYDIR# | InotifyConstants.IN_MASK_CREATE
    for route in dispatcher.routes:
        mask |= route.event & ~ExtendedInotifyConstants.EX_ALL_EX_EVENTS
    mask |= Route.parse_mask_from_str(settings.worker_extra_mask)
        
    logger.info(f'Using Inotify mask 0x{mask:08x} ({ExtendedEvent(mask).full_event_name})')

    tracker = FileTracker()
    for path in args.paths:
        tracker.watch_dir(path)

    controller = MasterController(dispatcher, tracker)

    workers = []
    if settings.worker_every_path:
        for path in args.paths:
            workers.append(Worker([path], dispatcher, controller, mask=mask))
    else:
        workers = [Worker(args.paths, dispatcher, controller, mask=mask)]
    
    # ===  Start threads  ===
    
    dispatcher.start()
    for worker in workers:
        worker.start()
    controller.start()
    logger.success(f'Monitor {args.name or ""}(pid {os.getpid()}) is running.')

    # ===  Join threads  ===

    # controller.close()  # NOTE: wait for controller closing itself
    for worker in workers:
        worker.join()
    if any(worker.is_crashed for worker in workers):
        return  # we may want to send alert about crashed workers, so keep dispatcher running
    dispatcher.close()


if __name__ == '__main__':
    import argparse
    from utils import overwrite_settings

    parser = argparse.ArgumentParser()
    parser.add_argument('paths', type=str, nargs='+')
    parser.add_argument('--name', type=str, default=None)
    args = overwrite_settings(parser)

    main(args)
