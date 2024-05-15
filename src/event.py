import os
import re
from typing import Iterable, List, Tuple, Iterator
from time import time
from linux import InotifyConstants
from loguru import logger
from datetime import datetime


class ExtendedInotifyConstants(InotifyConstants):
    EX_META = 0x100000000
    EX_RENAME = 0x200000000
    EX_BEGIN_MODIFY = 0x400000000
    EX_END_MODIFY = 0x800000000
    EX_MODIFY_CONFIG = 0x1000000000


class LinuxProcess:
    def __init__(self, pid: str) -> None:
        self._pid = pid
        try:
            self._exe = f'/proc/{pid}/exe'
        except:
            self._exe = None

    @staticmethod
    def get_procs_by_filename(path: str) -> Iterator['LinuxProcess']:
        try:
            pids = os.listdir('/proc')
        except:
            return
        for pid in pids:
            if not pid.isdigit():
                continue
            try:
                fds = os.listdir(f'/proc/{pid}/fd')
            except:
                continue
            for fd in fds:
                try:
                    if os.readlink(f'/proc/{pid}/fd/{fd}') == path:
                        yield LinuxProcess(pid)
                        break
                except:
                    pass

    def __str__(self) -> str:
        return self._pid


class InotifyEvent:
    def __init__(self, wd, mask, cookie, name, src_path, dest_path = None, event_time: float = None) -> None:
        self._wd = wd
        self._mask = mask
        self._cookie = cookie
        self._name = name
        self._src_path = src_path
        self._dest_path = dest_path
        self._time = time() if event_time is None else event_time
        self._proc = None

        self.lsb = self._mask & -self._mask
        self._event_name = None

    @classmethod
    def from_other(cls, other: 'InotifyEvent', mask=None, dest_path=None):
        return InotifyEvent(
            other._wd,
            other._mask if mask is None else other._mask | mask,
            other._cookie,
            other._name,
            other._src_path,
            dest_path=dest_path,
            event_time=other._time
        )

    def select_routes(self, routes: Iterable) -> Iterator:
        for route in routes:
            if route.event & self._mask and \
                    (route.pattern.fullmatch(self._src_path) or
                     self._dest_path is not None and route.pattern.fullmatch(self._dest_path)):
                yield route

    def select_procs(self) -> None:
        self._proc = list(LinuxProcess.get_procs_by_filename(os.fsdecode(self._src_path)))

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
    
    @property
    def event_name(self):
        if self._event_name is None:
            for event in (
                    'IN_ACCESS', 'IN_MODIFY', 'IN_ATTRIB', 'IN_CLOSE_WRITE',
                    'IN_CLOSE_NOWRITE','IN_OPEN', 'IN_MOVED_FROM', 'IN_MOVED_TO',
                    'IN_DELETE', 'IN_CREATE', 'IN_DELETE_SELF', 'IN_MOVE_SELF',
                    'IN_UNMOUNT', 'IN_Q_OVERFLOW', 'IN_IGNORED', 'EX_RENAME'):
                if self._mask & getattr(ExtendedInotifyConstants, event):
                    self._event_name = event
                    break  # TODO: Is it possible to have multiple user-space events?
        return self._event_name
    
    @property
    def full_event_name(self):
        masks = []
        for event in dir(ExtendedInotifyConstants):
            if not event.startswith('_'):
                mask = getattr(ExtendedInotifyConstants, event)
                if self._mask & mask >= mask:
                    masks.append(event)
        masks = '|'.join(masks)
        return masks
    
    def get_fields(self) -> dict:
        return {
            'ev_src': os.fsdecode(self._src_path) if self._src_path is not None else None,
            'ev_dest': os.fsdecode(self._dest_path) if self._dest_path is not None else None,
            'ev_time': datetime.fromtimestamp(self._time),
            'ev_name': self.full_event_name
        }
    
    def __repr__(self):
        return f'{self.__class__.__name__}({self.full_event_name}, {self._src_path}, {self._dest_path}, {self._time})'

    def __str__(self):
        return f'{self.event_name} {os.fsdecode(self._src_path)}'
    

class ExtendedEvent(InotifyEvent):
    def __init__(self, mask: int, src_path: bytes = b'', dest_path: bytes = None,
                 event_time: float = None) -> None:
        super().__init__(None, mask, None, None,
                         src_path=src_path, dest_path=dest_path, event_time=event_time)
        self.override = None  # TODO: This extended event may contain and override sub-events

    @property
    def event_name(self):
        if super().event_name is None:
            for event in ('EX_RENAME', 'EX_MODIFY_CONFIG'):
                if self._mask & getattr(ExtendedInotifyConstants, event):
                    self._event_name = event
                    break
        return self._event_name
