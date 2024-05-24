from abc import abstractmethod
from io import StringIO
import macros
import re
import os
import os.path as osp
import configparser
import json
import utils
import warnings
from typing import Dict, List, Tuple, TypeVar, Iterator
from threading import Thread, Lock
from loguru import logger
from database.conn import SQLConnection, SQLConnectionPool
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
    
    def save(self, path: str, indexer: BaseIndexer = None) -> None:
        if indexer is None:
            utils.save_json(self._data, path)
        else:
            pass

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}({self._data})'

    def __str__(self) -> str:
        return json.dumps(self._data)


class FileDiff(BaseRecord):
    def __init__(self, diff: dict, file_cls = None) -> None:
        super().__init__(diff)
        self._file_cls = file_cls

    @property
    def diff(self) -> dict:
        return self._data

    def __len__(self) -> int:  # used in `if diff` / `if not diff`
        return len(self._data)
    
    def to_tree(self) -> str:
        if self._file_cls is None:
            return json.dumps(self._data, indent=4)
        return self._file_cls.diff_to_tree(self._data)


class BaseFile(BaseRecord):
    format = 'BASE'

    def __init__(self, data: dict, path: str = None) -> None:
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
        return FileDiff(self._diff(self._data, other._data), self.__class__)

    @staticmethod
    def _diff(cfg1: dict, cfg2: dict) -> dict:
        pass

    def reset(self, diff: FileDiff):
        return self.__class__(self._reset(self._data, diff.diff), self._path)

    @staticmethod
    def _reset(cfg: dict, diff: dict) -> dict:
        pass
    
    @abstractmethod
    def to_raw(self) -> str:
        pass

    @staticmethod
    @abstractmethod
    def diff_to_tree(diff: dict) -> str:
        pass


def _dict_diff(sec1: dict, sec2: dict) -> dict:
    mod = {'add': {}, 'del': {}, 'mod': {}}
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
        return mod
    return {}


def _dict_reset(sec1: dict, sec2: dict, secd: dict):
    for k in set(sec2.keys()) | set(secd['del'].keys()):
        if k in secd['add']:
            pass
        elif k in secd['del']:
            sec1[k] = secd['del'][k]
        elif k in secd['mod']:
            sec1[k] = secd['mod'][k][0]
        else:
            sec1[k] = sec2[k]


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
        diff = {'add': {}, 'del': {}, 'mod': {}}
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
            sec1, sec2 = cfg1[s], cfg2[s]
            mod = _dict_diff(sec1, sec2)
            if mod:
                diff['mod'][s] = mod
        if diff['add'] or diff['del'] or diff['mod']:
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
                _dict_reset(sec1, sec2, secd)
            else:
                ret[s] = cfg[s]
        return ret
    
    def to_raw(self) -> str:
        with StringIO() as fo:
            for s, sec in self._data.items():
                if not sec:
                    continue
                print(f'\n[{s}]', file=fo)
                for k, v in sec.items():
                    print(f'{k} = {v}', file=fo)
            return fo.getvalue().lstrip('\n')
        
    @staticmethod
    def diff_to_tree(diff: Dict) -> str:
        with StringIO() as fo:
            for s, sec in diff['add'].items():
                print('+', f'[{s}]', file=fo)
                for k, v in sec.items():
                    print(' ', '+', f'{k}: {v}', file=fo)
            for s, sec in diff['del'].items():
                print('-', f'[{s}]', file=fo)
                for k, v in sec.items():
                    print(' ', '-', f'{k}: {v}', file=fo)
            for s, sec in diff['mod'].items():
                print('*', f'[{s}]', file=fo)
                for k, v in sec['add'].items():
                    print(' ', '+', f'{k}: {v}', file=fo)
                for k, v in sec['del'].items():
                    print(' ', '-', f'{k}: {v}', file=fo)
                for k, (v1, v2) in sec['mod'].items():
                    print(' ', '*', f'{k}: {v1} → {v2}', file=fo)
            return fo.getvalue()


class JsonFile(BaseFile):
    format = 'JSON'
    
    @staticmethod
    def _read(path: str) -> dict:
        try:
            return utils.load_json(path)
        except Exception as e:
            logger.error(e)
            return {}

    @staticmethod
    def _diff(cfg1: dict, cfg2: dict) -> Dict[str, List[Tuple[int, str]]]:
        return _dict_diff(cfg1, cfg2)

    @staticmethod
    def _reset(cfg: dict, diff: dict) -> dict:
        cfg1 = {}
        _dict_reset(cfg1, cfg, diff)
        return cfg1
    
    def to_raw(self) -> str:
        return str(self)
    
    @staticmethod
    def diff_to_tree(diff: Dict) -> str:
        with StringIO() as fo:
            for k, v in diff['add'].items():
                print('+', f'{k!r}: {v!r}', file=fo)
            for k, v in diff['del'].items():
                print('-', f'{k!r}: {v!r}', file=fo)
            for k, (v1, v2) in diff['mod'].items():
                print('*', f'{k!r}: {v1!r} → {v2!r}', file=fo)
            return fo.getvalue()


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
    
    def to_raw(self) -> str:
        return ''.join(self._data['lines'])
    
    @staticmethod
    def diff_to_tree(diff: Dict) -> str:
        with StringIO() as fo:
            for t, line, mod in diff['add/del']:
                print(f'{line:4d}', t, mod, end='', file=fo)
            return fo.getvalue()
    

FileT = TypeVar('FileT', bound=BaseFile)
_ABBR_LEN = 3
_file_cls = (IniFile, JsonFile, GenericFile)
_name_to_type: Dict[str, BaseFile] = {
    **{t.format: t for t in _file_cls},
    **{t.format[:_ABBR_LEN]: t for t in _file_cls if len(t.format) > _ABBR_LEN}
}


class BaseFileTracker:
    """
    File tracker with simple version control.
    """
    # TODO: (Optional) Optimize diff tree, e.g., 1 -> 2 -> 1 could be 2 <- 1 -> 1
    def __init__(self) -> None:
        self._patterns = settings.tracker_patterns
        self._filetypes: List[BaseFile] = [_name_to_type[name] for name in settings.tracker_filetypes]
        self._max_depth = settings.tracker_depth

        # self._index = self._fid_for_path = self._nr_index = None

        self._lock = Lock()

        self._indexer = None  # manage the index of backup files and diff files
        self._enabled = True
        self._cols = ('fid', 'path', 'version', 'format')
        self._indexer_type = settings.tracker_indexer
        self._cache_type = settings.tracker_cachetype

        if settings.tracker_cachetype == 'file':
            self._dir = settings.tracker_cachedir
            self._backup_dir = osp.join(self._dir, 'backup')
            self._diff_dir = osp.join(self._dir, 'diff')
            _create_dir(self._dir)
            _create_dir(self._backup_dir)
            _create_dir(self._diff_dir)

        if self._indexer_type == 'csv':
            self._index_file = osp.join(self._dir, 'index.csv')
            _create_file(self._index_file)

        else:
            self._pool = SQLConnectionPool(8)
            self._pool.init_conn()
            if not self._pool.enabled:
                logger.warning('Attempting to use SQL indexer but SQL is not enabled. '
                               'File tracker will be disabled.')
                self._enabled = False
            
    def skip_disabled(func):
        def inner(self, *args, **kwargs):
            if self._enabled:
                return func(self, *args, **kwargs)
        return inner
            
    def raise_disabled(func):
        def inner(self, *args, **kwargs):
            if self._enabled:
                return func(self, *args, **kwargs)
            else:
                raise RuntimeError("Tracker not enabled")
        return inner

    def _index(self, fid: int = None) -> Tuple[int, str, int, str]:
        return self._indexer.select(fid)
    
    @raise_disabled
    def __iter__(self) -> Iterator[Dict]:
        for fid, path, version, format in self._indexer.select():
            yield {'path': path, 'version': version, 'format': format}
    
    def _fid_for_path(self, path: str) -> str:
        return self._indexer.select2(path)

    @abstractmethod
    def _insert_index(self, path: str = None,
                      version: int = 0, format: str = 'INI', backup: BaseFile = None) -> int:
        pass

    @abstractmethod
    def _update_index(self, fid: int, path: str = None,
                      version_inc: int = 0, backup: BaseFile = None) -> int:
        pass

    @abstractmethod
    def _delete_index(self, fid: int) -> None:
        # TODO
        pass
    
    @abstractmethod
    def _load_backup(self, cls, fid: int) -> BaseFile:
        pass
            
    @abstractmethod
    def _insert_diff(self, fid: int, version: int, diff: FileDiff) -> None:
        pass

    @abstractmethod
    def _delete_diff(self, fid: int, version: int) -> None:
        pass

    @abstractmethod
    def _load_diff(self, fid: int, version: int) -> FileDiff:
        pass
    
    # Operations

    def _match_pattern(self, path: str) -> BaseFile:
        for pattern, filetype in zip(
                self._patterns, self._filetypes):
            if re.fullmatch(pattern, osp.abspath(path)) or \
                    re.fullmatch(pattern, osp.relpath(path)):
                return filetype.from_file(path)
    
    @skip_disabled
    def watch_or_compare(self, path: str, callback: callable = None) -> Thread:
        thread = Thread(target=self._watch_or_compare, args=(path, callback))
        thread.start()
        return thread
    
    def _watch_or_compare(self, path: str, callback: callable = None) -> None:
        if macros.TEST_TRACKER_DELAY:
            from time import time
            tic = time()

        # XXX: This function uses 2 or 3 SQL connections, which could be reduced to 1
        cfg = self._match_pattern(path)
        if cfg is None:
            return
        fid = self._fid_for_path(path)
        if fid is not None:
            cfg1, cfg2, diff = self._compare_file(fid, cfg)
            if diff and callback is not None:
                event = ExtendedEvent(
                    ExtendedInotifyConstants.EX_MODIFY_CONFIG, os.fsencode(path))
                event.add_field(f_before=cfg1, f_after=cfg2, f_diff=diff)
                callback(event)
        else:
            self._watch_file(cfg)

        if macros.TEST_TRACKER_DELAY:
            elapsed = time() - tic
            logger.trace(f'Tracker used {elapsed} secs processing {path}')

    @skip_disabled
    def watch_dir(self, path: str) -> None:
        for file in os.listdir(path):
            self._watch_or_compare(osp.join(path, file))

    def _watch_file(self, cfg: BaseFile) -> None:
        """Start tracking a file."""
        path = cfg.path
        fid = self._insert_index(path=path, format=cfg.format, backup=cfg)

    def _compare_file(self, fid: int, cfg2: BaseFile) -> Tuple[BaseFile, BaseFile, FileDiff]:
        """Compare current file with backup. Return diff if updated."""
        cfg1 = self._load_backup(cfg2.__class__, fid)
        diff = cfg1.diff(cfg2)
        if not diff:
            return None, None, None
        version = self._update_index(fid, version_inc=1, backup=cfg2)
        if self._max_depth != 0:
            self._insert_diff(fid, version, diff)
            # Find and remove out-of-date diff file
            _, _, latest_ver, _ = self._index(fid)
            if self._max_depth > 0:
                self._delete_diff(fid, latest_ver - self._max_depth)
        return cfg1, cfg2, diff

    @raise_disabled
    def checkout_file(self, path: str, version: int) -> BaseFile:
        """Checkout a specified version of a file and return as dict,
        without rewriting the file.
        NOTE: Rows will not be recoverd to the orignal order.
        """
        path = osp.abspath(path)
        fid = self._fid_for_path(path)
        if fid is None:
            raise KeyError(f"File {path} not being watched")
        _, _, latest_ver, format = self._index(fid)
        cfg = self._load_backup(_name_to_type[format], fid)

        target_ver = version if version >= 0 else latest_ver + version
        if target_ver < 0 or target_ver > latest_ver:
            raise ValueError(f"Invalid target version {target_ver}, "
                             f"current version is {latest_ver}")
        if latest_ver - target_ver > self._max_depth >= 0:
            warnings.warn("Target version exceeds maximum version depth")

        for ver in range(latest_ver, target_ver, -1):
            diff = self._load_diff(fid, ver)
            cfg = cfg.reset(diff)
        
        return cfg
    
    @raise_disabled
    def wipe(self) -> int:
        """Clear the index, backup and diff of unused files."""
        ids = {}
        for fid, path, version, format in self._indexer.select():
            if not osp.exists(path):
                ids[fid] = version
        self._wipe(ids)
        return len(ids)
    
    @abstractmethod
    def _wipe(self, ids) -> None:
        pass


class FileCacheTracker(BaseFileTracker):
    def __init__(self) -> None:
        super().__init__()

        if self._indexer_type == 'csv':
            self._indexer = CSVIndexer(self._index_file, self._cols)
        else:
            self._indexer = SQLIndexer('tracker_index', self._cols, self._pool)

    def _insert_index(self, path: str = None,
                      version: int = 0, format: str = 'INI', backup: BaseFile = None) -> int:
        fid = self._indexer.insert(path=path, version=version, format=format[:_ABBR_LEN])
        backup.save(self._get_head_path(fid))
        return fid

    def _update_index(self, fid: int, path: str = None,
                      version_inc: int = 0, backup: BaseFile = None) -> int:
        ret = self._indexer.update(fid, path=path,
                            version=(None if version_inc == 0 else lambda x: x+version_inc))
        backup.save(self._get_head_path(fid))
        return ret.get('version')
    
    def _load_backup(self, cls, fid: int) -> BaseFile:
        return cls.from_backup(self._get_head_path(fid))
        
    def _insert_diff(self, fid: int, version: int, diff: FileDiff) -> None:
        diff.save(self._get_diff_path(fid, version))

    def _delete_diff(self, fid: int, version: int) -> None:
        diff_file = self._get_diff_path(fid, version)
        if osp.exists(diff_file):
            os.remove(diff_file)

    def _load_diff(self, fid: int, version: int) -> FileDiff:
        return FileDiff.from_backup(self._get_diff_path(fid, version))
    
    def _get_head_path(self, fid: int) -> str:
        return osp.join(self._backup_dir, f'{fid}.json')

    def _get_diff_path(self, fid: int, version: int = None) -> str:
        if version is None:
            _, _, version, _ = self._index(fid)
        return osp.join(self._diff_dir, f'{fid}.{version}.json')
    
    def _wipe(self, ids: dict) -> None:
        self._indexer.delete(*ids)
        for fid, version in ids.items():
            backup_file = self._get_head_path(fid)
            if osp.exists(backup_file):
                os.remove(backup_file)
            for ver in range(1, version+1):
                self._delete_diff(fid, ver)


class SQLCacheTracker(BaseFileTracker):
    def __init__(self) -> None:
        super().__init__()

        if self._indexer_type == 'csv':
            logger.warning('tracker_index=csv is only compatible with tracker_cachetype=file. '
                            'File tracker will be disabled.')
            self._enabled = False
        else:
            self._indexer = SQLJsonIndexer('tracker_index', 'tracker_diff', self._cols, 'backup', 'diff', self._pool)

    def _insert_index(self, path: str = None,
                      version: int = 0, format: str = 'INI', backup: BaseFile = None) -> int:
        return self._indexer.insert(path=path, version=version, format=format[:_ABBR_LEN], backup=str(backup))

    def _update_index(self, fid: int, path: str = None,
                      version_inc: int = 0, backup: BaseFile = None) -> int:
        ret = self._indexer.update(fid, path=path,
                    version=(None if version_inc == 0 else lambda x: x+version_inc), backup=str(backup))
        return ret.get('version')

    def _load_backup(self, cls, fid: int) -> BaseFile:
        dic, = self._indexer.select(fid, cols=('backup',))
        return cls(json.loads(dic))

    def _insert_diff(self, fid: int, version: int, diff: FileDiff) -> None:
        self._indexer.insert_diff(fid=fid, version=version, diff=str(diff))

    def _delete_diff(self, fid: int, version: int) -> None:
        self._indexer.delete_diff(fid=fid, version=version)

    def _load_diff(self, fid: int, version: int) -> FileDiff:
        ret = self._indexer.select_diff(fid=fid, version=version)
        if ret is None:
            raise FileNotFoundError(f'fid.version {fid}.{version} not found')
        dic, = ret
        return FileDiff(json.loads(dic))
    
    def _wipe(self, ids) -> None:
        self._indexer.delete(*ids)


def FileTracker():
    if settings.tracker_cachetype == 'file':
        return FileCacheTracker()
    elif settings.tracker_cachetype == 'sql':
        return SQLCacheTracker()
    

def _test_generic():
    a = GenericFile.from_file('/home/user/test/configs/aa.py')
    print(a)
    b = GenericFile.from_file('/home/user/test/configs/bb.py')
    print(b)
    diff = a.diff(b)
    print(diff)
    print(b.reset(diff))
    

def _test_json():
    a = JsonFile.from_file('configs/example1.json')
    print(a)
    b = JsonFile.from_file('configs/example2.json')
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
    dbconn = SQLConnection()
    dbconn.init_conn()
    
    def clear_all():
        # Clear .track
        if osp.isdir(settings.tracker_cachedir):
            shutil.rmtree(settings.tracker_cachedir)
        # Truncate table
        with dbconn.cursor() as cursor:
            cursor.execute('TRUNCATE TABLE tracker_index')
            cursor.execute('TRUNCATE TABLE tracker_diff')
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
    t = tracker.watch_or_compare(files[1])
    print('Compare', t)
    t.join()
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
    settings.tracker_depth = 1
    tracker = FileTracker()
    print('Init')
    with open(files[0], 'w') as fo:
        print("""[example]
              a = 1""", file=fo)
    print('[Create]', files[0])
    t = tracker.watch_or_compare(files[0])
    print('Watch', files[0], t)
    t.join()
    with open(files[0], 'a') as fo:
        print('b = 2', file=fo)
    print('[Modify]', files[0])
    t = tracker.watch_or_compare(files[0])
    print('Compare', t)
    t.join()
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
    elif sys.argv[1] == 'json':
        _test_json()
