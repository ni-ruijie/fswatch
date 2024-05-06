import re
import os
import os.path as osp
import configparser
import csv
import utils


# .track
# ├── backup
# │   └── id1.json
# ├── diff
# │   ├── id1.0.json
# │   └── id1.1.json
# └── index.csv
#     : id1,path/to/file,2,INI


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
    def __init__(self, cachedir: str, pattern: str, max_depth: int = -1) -> None:
        self._dir = cachedir
        self._backup_dir = osp.join(self._dir, 'backup')
        self._diff_dir = osp.join(self._dir, 'diff')
        self._index_file = osp.join(self._dir, 'index.csv')
        self._pat = pattern

        self._index = self._fid_for_path = self._nr_index = None
        self.load_index()

    def _load_index(self) -> None:
        self._index = {}
        self._fid_for_path = {}
        self._nr_index = 0
        # fid,path,version,format
        with open(self._index_file, 'r') as fi:
            reader = csv.reader(fi)
            for line, (fid, path, version, format) in enumerate(reader):
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
        self._fid_for_path = fid
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
        # TODO: Replace one line of an index file

    def _delete_index(self, fid: None) -> None:
        # TODO
        raise NotImplementedError()
        _, path, _, _ = self._index[fid]
        del self._index[fid]
        del self._fid_for_path[path]

    def _create_fid(self) -> int:
        # TODO
        return self._nr_index

    @staticmethod
    def _read_config(path: str) -> dict:
        # TODO: Consider broken files (e.g., duplicate sections or keys)
        config = configparser.ConfigParser()
        config.read(path)
        return config

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
            k1, k2 = set(sec1.keys(), sec2.keys())
            add_keys = k2 - k1
            del_keys = k1 - k2
            com_keys = k1 & k2
            if not add_keys and not del_keys and not com_keys:
                continue
            for k in add_keys:
                mod['add'][k] = k2[k]
            for k in del_keys:
                mod['del'][k] = k1[k]
            for k in com_keys:
                mod['mod'][k] = (k1[k], k2[k])
            diff['mod'][s] = mod
        return mod
        
    @staticmethod
    def _reset_config(cfg: dict, diff: dict) -> dict:
        ret = {}
        for s in cfg:
            if s in diff['add']:
                pass
            elif s in diff['del']:
                ret[s] = diff['del'][s]
            elif s in diff['mod']:
                ret[s] = {}
                sec1, sec2, secd = ret[s], cfg[s], diff['mod'][s]
                for k in sec2:
                    if k in secd['add']:
                        pass
                    elif k in secd['del']:
                        sec1[k] = secd[k]
                    elif k in secd['mod']:
                        sec1[k] = secd[k][0]
                    else:
                        sec1[k] = sec2[k]
            else:
                ret[s] = cfg
        return ret
    
    def _get_head_path(self, fid: int) -> str:
        return osp.join(self._backup_dir, f'{fid}.json')

    def _get_diff_path(self, fid: int, version: int = None) -> str:
        if version is None:
            _, _, version, _ = self._index[fid]
            version += 1
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

    def compare_file(self, path: str) -> None:
        """Compare current file with backup."""
        fid = self._fid_for_path[path]
        cfg1, cfg2 = self._get_backup(fid), self._read_config(path)
        diff = self._diff_config(cfg1, cfg2)
        if not diff:
            return
        self._update_index(fid)
        utils.save_json(diff, self._get_diff_path(fid))

    def checkout_file(self, path: str, version: int) -> dict:
        """Checkout a specified version of a file and return as dict,
        without rewriting the file.
        """
        if path not in self._fid_for_path:
            raise KeyError("File not being watched")
        fid = self._fid_for_path[path]
        cfg = self._get_head_path(fid)

        target_ver = version
        _, _, latest_ver, _ = self._index[fid]
        for ver in range(latest_ver, target_ver, -1):
            diff = utils.load_json(self._get_diff_path(fid, ver))
            cfg = self._reset_config(cfg, diff)
        
        return cfg


if __name__ == '__main__':
    tracker = FileTracker()
    cfg1 = tracker.read_config('example.ini')
    cfg2 = tracker.read_config('example.ini')
    print(cfg1)
    print(tracker.diff_config(cfg1, cfg2))
