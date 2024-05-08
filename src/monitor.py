import ctypes
import struct
import threading
import errno
import os
import os.path as osp
import utils
from loguru import logger
from linux import *
from tracker import FileTracker
from dispatcher import BaseDispatcher, Dispatcher
from controller import MonitorController
import settings


EVENT_SIZE = ctypes.sizeof(inotify_event_struct)
DEFAULT_NUM_EVENTS = 2048
DEFAULT_EVENT_BUFFER_SIZE = DEFAULT_NUM_EVENTS * (EVENT_SIZE + 16)

tracker = FileTracker(settings.cache_dir, settings.tracked_pattern)


class InotifyEvent:
    def __init__(self, wd, mask, cookie, name, src_path):
        self._wd = wd
        self._mask = mask
        self._cookie = cookie
        self._name = name
        self._src_path = src_path

        self._event_name = 'UNDEFINED'
        lsb = self._mask & -self._mask
        for event in (
                'IN_ACCESS', 'IN_MODIFY', 'IN_ATTRIB', 'IN_CLOSE_WRITE',
                'IN_CLOSE_NOWRITE','IN_OPEN', 'IN_MOVED_FROM', 'IN_MOVED_TO',
                'IN_DELETE', 'IN_CREATE', 'IN_DELETE_SELF', 'IN_MOVE_SELF',
                'IN_UNMOUNT', 'IN_Q_OVERFLOW', 'IN_IGNORED'):
            if self._mask & getattr(InotifyConstants, event):
                self._event_name = event
                break  # TODO: Is it possible to have multiple user-space events?

    @property
    def is_dir(self):
        return self._mask & InotifyConstants.IN_ISDIR
    
    @property
    def is_create_file(self):
        return ~(self._mask & InotifyConstants.IN_ISDIR) and \
            self._mask & InotifyConstants.IN_CREATE
    
    @property
    def is_modify_file(self):
        return ~(self._mask & InotifyConstants.IN_ISDIR) and \
            self._mask & InotifyConstants.IN_MODIFY
    
    @property
    def is_delete_file(self):
        return ~(self._mask & InotifyConstants.IN_ISDIR) and \
            self._mask & InotifyConstants.IN_DELETE
    
    @property
    def is_create_dir(self):
        mask = InotifyConstants.IN_ISDIR | InotifyConstants.IN_CREATE
        return self._mask & mask >= mask
    
    @property
    def is_delete_dir(self):
        # mask = InotifyConstants.IN_ISDIR | InotifyConstants.IN_DELETE
        mask = InotifyConstants.IN_DELETE_SELF
        return self._mask & mask >= mask
    
    @property
    def is_overflow(self):
        return self._mask & InotifyConstants.IN_Q_OVERFLOW
    
    @property
    def is_ignored(self):
        return self._mask & InotifyConstants.IN_IGNORED

    def __str__(self):
        return f'{self._event_name} {os.fsdecode(self._src_path)}'


class Worker(threading.Thread):
    def __init__(self, path: str, channel: BaseDispatcher,
                 controller: MonitorController, watch_link: bool = True):
        super().__init__()
        self._stopped_event = threading.Event()
        self._path = os.fsencode(path)
        self._channel = channel
        self._controller = controller

        self._lock = threading.Lock()
        self._fd = inotify_init()
        logger.debug(f'fd: {self._fd}')
        self._wd_for_path = {}
        self._path_for_wd = {}
        # These are used for watching symbolic links
        self._links_for_path = {}  # target -> list of links
        self._path_for_link = {}  # link -> unique target

        self._watch_link = watch_link

        self._add_dir_watch(self._path)

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
                continue
            if mask & InotifyConstants.IN_IGNORED:
                continue

            wd_path = self._path_for_wd[wd]
            src_path = osp.join(wd_path, name) if name else wd_path  # avoid trailing slash
            # logger.debug(f'Event {mask:08x}')
            event = InotifyEvent(wd, mask, cookie, name, src_path)
            event_list.append(event)

            src_path_d = os.fsdecode(src_path)
            if event.is_create_file:
                if osp.islink(src_path):
                    self._add_link_watch(src_path)
                elif osp.isfile(src_path) and tracker.check_pattern(src_path_d):
                    tracker.watch_file(src_path_d)
            elif event.is_modify_file:
                if osp.islink(src_path):  # e.g., ln -sfn
                    self._rm_link_watch(src_path)
                    self._add_link_watch(src_path)
                elif osp.isfile(src_path) and tracker.check_pattern(src_path_d):
                    tracker.compare_file(src_path_d)
            elif event.is_delete_file:
                if src_path in self._path_for_link:
                    self._rm_link_watch(src_path)
                # TODO: handle deletion in file tracker
            
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

    def run(self):
        while not self._stopped_event.is_set():
            for event in self._read_events():
                continue
                self._channel.emit(self._channel.gen_data_msg(msg=str(event)))


def main(args):
    dispatcher = Dispatcher()
    controller = MonitorController(dispatcher)
    logger.info(f'Monitoring {args.path}.')

    worker = Worker(args.path, dispatcher, controller)
    worker.start()
    
    worker.join()
    dispatcher.close()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('path', type=str)

    # Add settings.* to exec options
    items = []
    for item in dir(settings):
        if not item.startswith('_'):
            value = getattr(settings, item)
            parser.add_argument(f'--{item}', type=type(value), default=None)
            items.append(item)

    args = parser.parse_args()

    for item in items:
        if getattr(args, item) is not None:
            setattr(settings, item, getattr(args, item))
            logger.info(f'settings.{item} = {getattr(args, item)}')

    main(args)
