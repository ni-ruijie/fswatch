import os
import re
from typing import Iterable, List, Tuple, Iterator
from linux import InotifyConstants


class ExtendedInotifyConstants(InotifyConstants):
    EX_META = 0x100000000
    EX_RENAME = 0x200000000


class InotifyEvent:
    def __init__(self, wd, mask, cookie, name, src_path, dest_path = None):
        self._wd = wd
        self._mask = mask
        self._cookie = cookie
        self._name = name
        self._src_path = src_path
        self._dest_path = dest_path

        self._event_name = 'UNDEFINED'
        self.lsb = self._mask & -self._mask
        for event in (
                'IN_ACCESS', 'IN_MODIFY', 'IN_ATTRIB', 'IN_CLOSE_WRITE',
                'IN_CLOSE_NOWRITE','IN_OPEN', 'IN_MOVED_FROM', 'IN_MOVED_TO',
                'IN_DELETE', 'IN_CREATE', 'IN_DELETE_SELF', 'IN_MOVE_SELF',
                'IN_UNMOUNT', 'IN_Q_OVERFLOW', 'IN_IGNORED', 'EX_RENAME'):
            if self._mask & getattr(ExtendedInotifyConstants, event):
                self._event_name = event
                break  # TODO: Is it possible to have multiple user-space events?

    @classmethod
    def from_other(cls, other, mask=None, dest_path=None):
        return InotifyEvent(
            other._wd,
            other._mask if mask is None else mask,
            other._cookie,
            other._name,
            other._src_path,
            dest_path)

    def select_routes(self, routes: Iterable[Tuple[str, re.Pattern, int]]) -> Iterator[str]:
        for tag, pattern, event in routes:
            if event & self._mask and \
                    (pattern.fullmatch(self._src_path) or
                     self._dest_path and pattern.fullmatch(self._dest_path)):
                yield tag

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