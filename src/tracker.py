import re
import os
import os.path as osp
import configparser
import csv
import utils
import warnings


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
    # TODO: Be thread-safe
    # TODO: Support more formats than INI
    # TODO: (Optional) Optimize diff tree, e.g., 1 -> 2 -> 1 could be 2 <- 1 -> 1
    def __init__(self, cachedir: str, pattern: str, max_depth: int = -1) -> None:
        self._dir = cachedir
        self._backup_dir = osp.join(self._dir, 'backup')
        self._diff_dir = osp.join(self._dir, 'diff')
        self._index_file = osp.join(self._dir, 'index.csv')
        self._pat = pattern
        self._max_depth = max_depth

        self._index = self._fid_for_path = self._nr_index = None

        _create_dir(self._dir)
        _create_dir(self._backup_dir)
        _create_dir(self._diff_dir)
        _create_file(self._index_file)

        self._load_index()

    def _load_index(self) -> None:
        self._index = {}
        self._fid_for_path = {}
        self._nr_index = 0
        # fid,path,version,format
        with open(self._index_file, 'r') as fi:
            reader = csv.reader(fi)
            line = -1
            for line, (fid, path, version, format) in enumerate(reader):
                fid, version = int(fid), int(version)
                self._index[fid] = (line, path, version, format)
                self._fid_for_path[path] = fid
            self._nr_index = line + 1

    def _insert_index(self, fid: int = None, path: str = None,
                      version: int = 0, format: str = 'INI') -> int:
        fid = fid or self._create_fid()
        path = path or ''
        line = self._nr_index
        self._nr_index += 1
        self._index[fid] = (line, path, version, format)
        self._fid_for_path[path] = fid
        with open(self._index_file, 'a') as fo:
            writer = csv.writer(fo)
            writer.writerow((fid, path, version, format))
        return fid

    def _update_index(self, fid: int, path: str = None,
                      version_inc: int = 0) -> None:
        _line, _path, _version, _format = self._index[fid]
        _path = path or _path
        _version += version_inc
        self._index[fid] = (_line, _path, _version, _format)
        # TODO: Replace one line of an index file instead of full reflushing
        with open(self._index_file, 'w') as fo:
            writer = csv.writer(fo)
            for fid, (line, path, version, format) in sorted(
                    self._index.items(), key=lambda kv: kv[1][0]):
                writer.writerow((fid, path, version, format))

    def _delete_index(self, fid: None) -> None:
        # TODO
        raise NotImplementedError()
        _, path, _, _ = self._index[fid]
        del self._index[fid]
        del self._fid_for_path[path]

    def _create_fid(self) -> int:
        # TODO: Use inode number or UID instead of line number
        return self._nr_index

    @staticmethod
    def _read_config(path: str) -> dict:
        # TODO: Consider broken files (e.g., duplicate sections or keys)
        config = configparser.ConfigParser()
        config.read(path)
        ret = {}
        for s in config:
            ret[s] = {}
            for k in config[s]:
                ret[s][k] = config[s][k]
        return ret

    @staticmethod
    def _diff_config(cfg1: dict, cfg2: dict) -> dict:
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
    def _reset_config(cfg: dict, diff: dict) -> dict:
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
    
    def _get_head_path(self, fid: int) -> str:
        return osp.join(self._backup_dir, f'{fid}.json')

    def _get_diff_path(self, fid: int, version: int = None) -> str:
        if version is None:
            _, _, version, _ = self._index[fid]
        return osp.join(self._diff_dir, f'{fid}.{version}.json')
    
    # Operations

    def check_pattern(self, path: str) -> bool:
        return re.fullmatch(self._pat, osp.abspath(path)) or \
            re.fullmatch(self._pat, osp.relpath(path))

    def watch_file(self, path: str) -> None:
        """Start tracking a file."""
        cfg = self._read_config(path)
        fid = self._insert_index(path=path)
        utils.save_json(cfg, self._get_head_path(fid))

    def compare_file(self, path: str) -> bool:
        """Compare current file with backup. Return True if updated."""
        fid = self._fid_for_path[path]
        cfg1 = utils.load_json(self._get_head_path(fid))
        cfg2 = self._read_config(path)
        diff = self._diff_config(cfg1, cfg2)
        if not diff:
            return False
        self._update_index(fid, version_inc=1)
        utils.save_json(cfg2, self._get_head_path(fid))
        if self._max_depth != 0:
            utils.save_json(diff, self._get_diff_path(fid))
            # Find and remove out-of-date diff file
            _, _, latest_ver, _ = self._index[fid]
            diff_file = self._get_diff_path(fid, latest_ver - self._max_depth)
            if self._max_depth > 0 and osp.exists(diff_file):
                os.remove(diff_file)
        return True

    def checkout_file(self, path: str, version: int) -> dict:
        """Checkout a specified version of a file and return as dict,
        without rewriting the file.
        NOTE: Rows will not be recoverd to the orignal order.
        """
        if path not in self._fid_for_path:
            raise KeyError("File not being watched")
        fid = self._fid_for_path[path]
        cfg = utils.load_json(self._get_head_path(fid))

        target_ver = version
        _, _, latest_ver, _ = self._index[fid]
        if target_ver < 0 or target_ver > latest_ver:
            raise ValueError(f"Invalid target version {target_ver}, "
                             f"current version is {latest_ver}")
        if latest_ver - target_ver > self._max_depth >= 0:
            warnings.warn("Target version exceeds maximum version depth")

        for ver in range(latest_ver, target_ver, -1):
            diff = utils.load_json(self._get_diff_path(fid, ver))
            cfg = self._reset_config(cfg, diff)
        
        return cfg


def _test_tracker():
    import os, shutil
    import settings
    import pytest
    files = (
        '/home/user/test/configs/foo.ini',
        '/home/user/test/configs/bar.ini'
    )
    
    # Clear .track
    shutil.rmtree(settings.cache_dir)
    
    print('===  Basic Test  ===')
    tracker = FileTracker(settings.cache_dir, settings.tracked_pattern)
    print('Init')
    print('Index', tracker._index)
    with open(files[0], 'w') as fo:
        pass
    print('[Create]', files[0])
    tracker.watch_file(files[0])
    print('Watch', files[0])
    with open(files[1], 'w') as fo:
        print("""[example]
              a = 1
              b = 2
              c = 3""", file=fo)
    print('[Create]', files[1])
    tracker.watch_file(files[1])
    print('Watch', files[1])
    print('Index', tracker._index)
    print('Compare', tracker.compare_file(files[1]))
    with open(files[1], 'w') as fo:
        print("""[example]
              b = 3
              d = 4
              [new]
              a = 2""", file=fo)
    print('[Modify]', files[1])
    print('Compare', tracker.compare_file(files[1]))
    print('Index', tracker._index)
    with open(files[1], 'w') as fo:
        print("""[example]
              b = 3
              c = 6""", file=fo)
    print('[Modify]', files[1])
    print('Compare', tracker.compare_file(files[1]))
    print('Index', tracker._index)
    
    print('===  Version Test  ===')
    print('Version', tracker.checkout_file(files[1], 0))

    print('===  Durability Test  ===')
    tracker = FileTracker(settings.cache_dir, settings.tracked_pattern)
    print('Init')
    print('Index', tracker._index)

    print('===  Version depth test  ===')
    shutil.rmtree(settings.cache_dir)
    print('[Clear]')
    tracker = FileTracker(settings.cache_dir, settings.tracked_pattern, max_depth=1)
    print('Init')
    with open(files[0], 'w') as fo:
        print("""[example]
              a = 1""", file=fo)
    print('[Create]', files[0])
    tracker.watch_file(files[0])
    print('Watch', files[0])
    with open(files[0], 'a') as fo:
        print('b = 2', file=fo)
    print('[Modify]', files[0])
    print('Compare', tracker.compare_file(files[0]))
    with open(files[0], 'a') as fo:
        print('c = 3', file=fo)
    print('[Modify]', files[0])
    print('Compare', tracker.compare_file(files[0]))

    print('===  Exception Test  ===')
    with pytest.raises(KeyError):
        tracker.checkout_file('', 0)
    with pytest.raises(FileNotFoundError):
        tracker.checkout_file(files[0], 0)


if __name__ == '__main__':
    ret = input('Run a test on the tracker. '
                'This will clear the .track directory. [y]/n: ')
    if not ret or ret == 'y':
        _test_tracker()
