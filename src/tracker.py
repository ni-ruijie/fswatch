import re
import os
import os.path as osp
import configparser
import csv
import utils
import warnings
from typing import Dict, List, Tuple, TypeVar, Iterator
from threading import Thread, Lock
from loguru import logger
from database.conn import SQLConnectionPool, dbconn
from database.indexer import *
from event import ExtendedInotifyConstants, ExtendedEvent
import settings


# .track
# ├── backup
# │   └── id1.json
# ├── diff
# │   ├── id1.0.json
# │   └── id1.1.json
# └── index.csv
#     : id1,path/to/file,2,INI


def _create_dir(path: str) -> bool:
    if osp.isdir(path):
        return False
    os.mkdir(path)
    return True


def _create_file(path: str) -> bool:
    if osp.isfile(path):
        return False
    with open(path, 'w') as fo:
        pass
    return True


class BaseRecord:
    def __init__(self, data: dict) -> None:
        self._data = data

    @classmethod
    def from_backup(cls, path: str):
        return cls(utils.load_json(path), path)
    
    def save(self, path: str) -> None:
        utils.save_json(self._data, path)

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}({self._data})'

    def __str__(self) -> str:
        return f'{self._data}'


class FileDiff(BaseRecord):
    def __init__(self, diff: dict, *args) -> None:
        super().__init__(diff)

    @property
    def diff(self) -> dict:
        return self._data

    def __len__(self) -> int:  # used in `if diff` / `if not diff`
        return len(self._data)


class BaseFile(BaseRecord):
    format = 'BASE'

    def __init__(self, data: dict, path: str) -> None:
        super().__init__(data)
        self._path = path

    @property
    def path(self):
        return self._path

    @classmethod
    def from_file(cls, path: str):
        data = cls._read(path)
        return cls(data, path) if data else None

    @staticmethod
    def _read(path: str) -> dict:
        pass

    def diff(self, other) -> FileDiff:
        return FileDiff(self._diff(self._data, other._data))

    @staticmethod
    def _diff(cfg1: dict, cfg2: dict) -> dict:
        pass

    def reset(self, diff: FileDiff):
        return self.__class__(self._reset(self._data, diff.diff), self._path)

    @staticmethod
    def _reset(cfg: dict, diff: dict) -> dict:
        pass


class IniFile(BaseFile):
    format = 'INI'

    @staticmethod
    def _read(path: str) -> dict:
        config = configparser.ConfigParser()
        try:
            config.read(path)
        except configparser.Error as e:
            logger.error(e)
            return {}
        ret = {}
        for s in config:
            ret[s] = {}
            for k in config[s]:
                ret[s][k] = config[s][k]
        return ret

    @staticmethod
    def _diff(cfg1: dict, cfg2: dict) -> dict:
        # TODO: (Optional) Track file renaming and section renaming
        diff = {'add': {}, 'del': {}, 'mod': {}, 'info': {}}
        # Compare section names
        s1, s2 = set(cfg1.keys()), set(cfg2.keys())
        add_secs = s2 - s1
        del_secs = s1 - s2
        com_secs = s1 & s2
        for s in add_secs:
            diff['add'][s] = cfg2[s]
        for s in del_secs:
            diff['del'][s] = cfg1[s]
        # Compare each section
        for s in com_secs:
            mod = {'add': {}, 'del': {}, 'mod': {}}
            sec1, sec2 = cfg1[s], cfg2[s]
            k1, k2 = set(sec1.keys()), set(sec2.keys())
            add_keys = k2 - k1
            del_keys = k1 - k2
            com_keys = k1 & k2
            for k in add_keys:
                mod['add'][k] = sec2[k]
            for k in del_keys:
                mod['del'][k] = sec1[k]
            for k in com_keys:
                if sec1[k] != sec2[k]:
                    mod['mod'][k] = (sec1[k], sec2[k])
            if mod['add'] or mod['del'] or mod['mod']:
                diff['mod'][s] = mod
        if diff['add'] or diff['del'] or diff['mod'] or diff['info']:
            return diff
        return {}

    @staticmethod
    def _reset(cfg: dict, diff: dict) -> dict:
        ret = {}
        for s in set(cfg.keys()) | set(diff['del'].keys()):
            if s in diff['add']:
                pass
            elif s in diff['del']:
                ret[s] = diff['del'][s]
            elif s in diff['mod']:
                ret[s] = {}
                sec1, sec2, secd = ret[s], cfg[s], diff['mod'][s]
                for k in set(sec2.keys()) | set(secd['del'].keys()):
                    if k in secd['add']:
                        pass
                    elif k in secd['del']:
                        sec1[k] = secd['del'][k]
                    elif k in secd['mod']:
                        sec1[k] = secd['mod'][k][0]
                    else:
                        sec1[k] = sec2[k]
            else:
                ret[s] = cfg[s]
        return ret


class GenericFile(BaseFile):
    format = 'GENERIC'
    
    @staticmethod
    def _read(path: str) -> dict:
        try:
            with open(path, 'r') as fi:
                return {'lines': fi.readlines()}
        except Exception as e:
            logger.error(e)
            return {}

    @staticmethod
    def _diff(cfg1: dict, cfg2: dict) -> Dict[str, List[Tuple[int, str]]]:
        """Myers diff algorithm."""

        a, b = cfg1['lines'], cfg2['lines']
        front = {1: (0, [])}

        for d in range(0, len(a) + len(b) + 1):
            for k in range(-d, d + 1, 2):
                go_down = k == -d or (k != d and front[k - 1][0] < front[k + 1][0])

                if go_down:
                    old_x, history = front[k + 1]
                    x = old_x
                else:
                    old_x, history = front[k - 1]
                    x = old_x + 1
                y = x - k

                history = history[:]

                if 1 <= y <= len(b) and go_down:
                    history.append(('+', y - 1))
                elif 1 <= x <= len(a):
                    history.append(('-', x - 1))

                while x < len(a) and y < len(b) and a[x] == b[y]:
                    x += 1
                    y += 1
                    history.append(('*', x - 1))

                if x >= len(a) and y >= len(b):
                    ret = {'add/del': []}
                    for t, line in history:
                        if t == '+':
                            ret['add/del'].append((t, line, b[line]))
                        elif t == '-':
                            ret['add/del'].append((t, line, a[line]))
                    if ret['add/del']:
                        return ret
                    return {}

                front[k] = x, history

    @staticmethod
    def _reset(cfg: dict, diff: dict) -> dict:
        b = cfg['lines']
        a = []
        cur_line = 0
        for t, mod_line, mod in diff['add/del']:
            if t == '+':
                for cur_line in range(cur_line, mod_line):
                    a.append(b[cur_line])
                cur_line = mod_line + 1
            elif t == '-':
                while len(a) < mod_line:
                    a.append(b[cur_line])
                    cur_line += 1
                a.append(mod)
        for cur_line in range(cur_line, len(b)):
            a.append(b[cur_line])
        return {'lines': a}
    

FileT = TypeVar('FileT', bound=BaseFile)
_ABBR_LEN = 3
_file_cls = (IniFile, GenericFile)
_name_to_type: Dict[str, BaseFile] = {
    **{t.format: t for t in _file_cls},
    **{t.format[:_ABBR_LEN]: t for t in _file_cls if len(t.format) > _ABBR_LEN}
}


class FileTracker:
    """
    File tracker with simple version control.

    Parameters
    ----------
    cachedir : str
        Directory to store tracking logs.
    pattern : str
        Pattern of files to be tracked.
    max_depth : int
        Maximum number of versions for a file. 0 for disable the tracker.
        The default is -1 for an infinite depth of versions.

    Methods
    -------
    watch_file
    compare_file
    checkout_file
    """
    # TODO: (Optional) Optimize diff tree, e.g., 1 -> 2 -> 1 could be 2 <- 1 -> 1
    def __init__(self, max_depth: int = -1) -> None:
        self._dir = settings.tracker_cachedir
        self._backup_dir = osp.join(self._dir, 'backup')
        self._diff_dir = osp.join(self._dir, 'diff')
        self._patterns = settings.tracker_patterns
        self._filetypes: List[BaseFile] = [_name_to_type[name] for name in settings.tracker_filetypes]
        self._max_depth = max_depth

        # self._index = self._fid_for_path = self._nr_index = None

        self._lock = Lock()

        _create_dir(self._dir)
        _create_dir(self._backup_dir)
        _create_dir(self._diff_dir)

        self._indexer = None  # manage the index of backup files and diff files
        self._enabled = True
        cols = ('fid', 'path', 'version', 'format')
        if settings.tracker_indexer == 'csv':
            self._index_file = osp.join(self._dir, 'index.csv')
            _create_file(self._index_file)
            self._indexer = CSVIndexer(self._index_file, cols)
        else:
            pool = SQLConnectionPool(8)
            pool.init_conn()
            self._indexer = SQLIndexer('tracked_index', cols, pool)
            if not pool.enabled:
                logger.warning('Attempting to use SQL indexer but SQL is not enabled. '
                               'File tracker will be disabled.')
                self._enabled = False
        if self._enabled:
            logger.success(f'FileTracker: Using {self._indexer.__class__.__name__} as indexer.')
            
    def if_enabled(func):
        def inner(self, *args, **kwargs):
            if self._enabled:
                return func(self, *args, **kwargs)
        return inner

    def _index(self, fid: int = None) -> Tuple[int, str, int, str]:
        return self._indexer.select(fid)
    
    @if_enabled
    def __iter__(self) -> Iterator[Dict]:
        for fid, path, version, format in self._indexer.select():
            yield {'path': path, 'version': version, 'format': format}
    
    def _fid_for_path(self, path: str) -> str:
        return self._indexer.select2(path)

    def _insert_index(self, path: str = None,
                      version: int = 0, format: str = 'INI') -> int:
        return self._indexer.insert(path=path, version=version, format=format[:_ABBR_LEN])

    def _update_index(self, fid: int, path: str = None,
                      version_inc: int = 0) -> None:
        self._indexer.update(fid, path=path,
                             version=(None if version_inc == 0 else lambda x: x+version_inc))

    def _delete_index(self, fid: None) -> None:
        # TODO
        raise NotImplementedError()

    @staticmethod
    def _read_config(path: str) -> dict:
        return IniFile._read(path)

    @staticmethod
    def _diff_config(cfg1: dict, cfg2: dict) -> dict:
        return IniFile._diff(cfg1, cfg2)
        
    @staticmethod
    def _reset_config(cfg: dict, diff: dict) -> dict:
        return IniFile._reset(cfg, diff)
    
    def _get_head_path(self, fid: int) -> str:
        return osp.join(self._backup_dir, f'{fid}.json')

    def _get_diff_path(self, fid: int, version: int = None) -> str:
        if version is None:
            _, _, version, _ = self._index(fid)
        return osp.join(self._diff_dir, f'{fid}.{version}.json')
    
    # Operations

    def _match_pattern(self, path: str) -> BaseFile:
        for pattern, filetype in zip(
                self._patterns, self._filetypes):
            if re.fullmatch(pattern, osp.abspath(path)) or \
                    re.fullmatch(pattern, osp.relpath(path)):
                return filetype.from_file(path)
    
    @if_enabled
    def watch_or_compare(self, path: str, callback: callable = None) -> Thread:
        thread = Thread(target=self._watch_or_compare, args=(path, callback))
        thread.start()
        return thread
    
    def _watch_or_compare(self, path: str, callback: callable = None) -> None:
        # XXX: This function uses 2 or 3 SQL connections, which could be reduced to 1
        cfg = self._match_pattern(path)
        if cfg is None:
            return
        with self._lock:
            fid = self._fid_for_path(path)
            if fid is not None:
                ret = self._compare_file(fid, cfg)
                if ret and callback is not None:
                    callback(ExtendedEvent(
                        ExtendedInotifyConstants.EX_MODIFY_CONFIG, os.fsencode(path)))
            else:
                self._watch_file(cfg)

    @if_enabled
    def watch_dir(self, path: str) -> None:
        for file in os.listdir(path):
            self._watch_or_compare(osp.join(path, file))

    def _watch_file(self, cfg: BaseFile) -> None:
        """Start tracking a file."""
        path = cfg.path
        fid = self._insert_index(path=path, format=cfg.format)
        cfg.save(self._get_head_path(fid))

    def _compare_file(self, fid: int, cfg2: BaseFile) -> bool:
        """Compare current file with backup. Return True if updated."""
        cfg1 = cfg2.__class__.from_backup(self._get_head_path(fid))
        diff = cfg1.diff(cfg2)
        if not diff:
            return False
        self._update_index(fid, version_inc=1)
        cfg2.save(self._get_head_path(fid))
        if self._max_depth != 0:
            diff.save(self._get_diff_path(fid))
            # Find and remove out-of-date diff file
            _, _, latest_ver, _ = self._index(fid)
            diff_file = self._get_diff_path(fid, latest_ver - self._max_depth)
            if self._max_depth > 0 and osp.exists(diff_file):
                os.remove(diff_file)
        return True

    @if_enabled
    def checkout_file(self, path: str, version: int) -> dict:
        """Checkout a specified version of a file and return as dict,
        without rewriting the file.
        NOTE: Rows will not be recoverd to the orignal order.
        """
        path = osp.abspath(path)
        fid = self._fid_for_path(path)
        if fid is None:
            raise KeyError("File not being watched")
        _, _, latest_ver, format = self._index(fid)
        cfg = _name_to_type[format].from_backup(self._get_head_path(fid))

        target_ver = version if version >= 0 else latest_ver + version
        if target_ver < 0 or target_ver > latest_ver:
            raise ValueError(f"Invalid target version {target_ver}, "
                             f"current version is {latest_ver}")
        if latest_ver - target_ver > self._max_depth >= 0:
            warnings.warn("Target version exceeds maximum version depth")

        for ver in range(latest_ver, target_ver, -1):
            diff = FileDiff.from_backup(self._get_diff_path(fid, ver))
            cfg = cfg.reset(diff)
        
        return cfg
    

def _test_generic():
    a = GenericFile.from_file('/home/user/test/configs/aa.py')
    print(a)
    b = GenericFile.from_file('/home/user/test/configs/bb.py')
    print(b)
    diff = a.diff(b)
    print(diff)
    print(b.reset(diff))


def _test_tracker():
    import os, shutil
    import settings
    class raises:
        def __init__(self, expected_exception):
            self._expected = expected_exception
        def __enter__(self):
            pass
        def __exit__(self, exc_type, exc_value, exc_tb):
            if exc_type is None:
                raise Exception('No exception')
            if exc_type == self._expected:
                return True

    files = (
        osp.expanduser('~/test/configs/foo.ini'),
        osp.expanduser('~/test/configs/bar.ini')
    )
    dbconn.init_conn()
    
    def clear_all():
        # Clear .track
        if osp.isdir(settings.tracker_cachedir):
            shutil.rmtree(settings.tracker_cachedir)
        # Truncate table
        with dbconn.cursor() as cursor:
            cursor.execute('TRUNCATE TABLE tracked_index')
        print('[Clear]')
    
    print('===  Basic Test  ===')
    clear_all()
    tracker = FileTracker()
    print('Init')
    print('Index', tracker._index())
    with open(files[0], 'w') as fo:
        pass
    print('[Create]', files[0])
    t = tracker.watch_or_compare(files[0])
    print('Watch', files[0], t)
    with open(files[1], 'w') as fo:
        print("""[example]
              a = 1
              b = 2
              c = 3""", file=fo)
    print('[Create]', files[1])
    t = tracker.watch_or_compare(files[1])
    print('Watch', files[1], t)
    t.join()
    print('Index', tracker._index())
    print('Compare', tracker.watch_or_compare(files[1]))
    with open(files[1], 'w') as fo:
        print("""[example]
              b = 3
              d = 4
              [new]
              a = 2""", file=fo)
    print('[Modify]', files[1])
    t = tracker.watch_or_compare(files[1])
    print('Compare', t)
    t.join()
    print('Index', tracker._index())
    with open(files[1], 'w') as fo:
        print("""[example]
              b = 3
              c = 6""", file=fo)
    print('[Modify]', files[1])
    t = tracker.watch_or_compare(files[1])
    print('Compare', t)
    t.join()
    print('Index', tracker._index())
    
    print('===  Version Test  ===')
    with tracker._lock:
        print('Version', tracker.checkout_file(files[1], 0))

    print('===  Durability Test  ===')
    tracker = FileTracker()
    print('Init')
    print('Index', tracker._index())

    print('===  Version depth test  ===')
    clear_all()
    tracker = FileTracker(max_depth=1)
    print('Init')
    with open(files[0], 'w') as fo:
        print("""[example]
              a = 1""", file=fo)
    print('[Create]', files[0])
    t = tracker.watch_or_compare(files[0])
    print('Watch', files[0], t)
    with open(files[0], 'a') as fo:
        print('b = 2', file=fo)
    print('[Modify]', files[0])
    t = tracker.watch_or_compare(files[0])
    print('Compare', t)
    with open(files[0], 'a') as fo:
        print('c = 3', file=fo)
    print('[Modify]', files[0])
    t = tracker.watch_or_compare(files[0])
    print('Compare', t)

    print('===  Exception Test  ===')
    t.join()
    with raises(KeyError):
        tracker.checkout_file('', 0)
    with raises(FileNotFoundError):
        tracker.checkout_file(files[0], 0)


if __name__ == '__main__':
    import sys
    if len(sys.argv) == 1 or sys.argv[1] == 'tracker':
        ret = input('Run a test on the tracker. '
                    'This will clear the .track directory and truncate the table. [y]/n: ')
        if not ret or ret == 'y':
            _test_tracker()
    elif sys.argv[1] == 'generic':
        _test_generic()
