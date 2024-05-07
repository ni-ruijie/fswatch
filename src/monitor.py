import ctypes
import struct
import threading
import pika
import errno
import os
import utils
from loguru import logger
from linux import *
from tracker import FileTracker
from dispatcher import Dispatcher
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
    def __init__(self, path, channel):
        super().__init__()
        self._stopped_event = threading.Event()
        self._path = os.fsencode(path)
        self._channel = channel

        self._lock = threading.Lock()
        self._fd = inotify_init()
        logger.debug(f'fd: {self._fd}')
        self._wd_for_path = {}
        self._path_for_wd = {}

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
        
        event_list = []
        for wd, mask, cookie, name in self._parse_event_buffer(event_buffer):
            if mask & (InotifyConstants.IN_Q_OVERFLOW | InotifyConstants.IN_IGNORED):
                continue
            wd_path = self._path_for_wd[wd]
            src_path = os.path.join(wd_path, name) if name else wd_path  # avoid trailing slash
            logger.debug(f'Event {mask:08x}')
            event = InotifyEvent(wd, mask, cookie, name, src_path)
            event_list.append(event)

            src_path_d = os.fsdecode(src_path)
            if event.is_create_file:
                if tracker.check_pattern(src_path_d):
                    tracker.watch_file(src_path_d)
            if event.is_modify_file:
                if tracker.check_pattern(src_path_d):
                    tracker.compare_file(src_path_d)
            
            if event.is_create_dir:
                self._add_dir_watch(src_path)
            
            elif event.is_delete_dir:
                self._rm_watch(wd)

        return event_list
    
    def _add_dir_watch(self, path, mask=InotifyConstants.IN_ALL_EVENTS):
        if not os.path.isdir(path):
            raise OSError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), path)
        self._add_watch(path, mask)

        for root, dirnames, _ in os.walk(path):
            for dirname in dirnames:
                full_path = os.path.join(root, dirname)
                if os.path.islink(full_path):  # TODO: To watch links or not?
                    continue
                self._add_watch(full_path, mask)
        
    def _add_watch(self, path, mask=InotifyConstants.IN_ALL_EVENTS):
        # TODO: Add and update watches recursively, both init and update
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
                self._channel.emit(self._channel.gen_data_msg(msg=str(event)))


def main(args):
    dispatcher = Dispatcher()
    logger.info(f'Monitoring {args.path}.')

    worker = Worker(args.path, dispatcher)
    worker.start()
    
    worker.join()
    dispatcher.close()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('path', type=str)
    args = parser.parse_args()

    logger.info(f'Inotify info: {utils.get_inotify_info()}')

    main(args)
