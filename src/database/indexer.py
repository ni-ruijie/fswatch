from typing import Callable, Iterable
import collections.abc
from abc import abstractmethod
import csv
from database.conn import SQLConnection


__all__ = ['BaseIndexer', 'CSVIndexer', 'SQLIndexer']


class BaseIndexer:
    def __init__(self, cols):  # default: fid, path, version, format
        self._cols = cols
        self._primary = cols[0]  # primary key a.k.a. key
        self._secondary = cols[1]  # secondary key a.k.a. key2
    
    @abstractmethod
    def select(self, key=None) -> tuple:
        """primary -> (primary, secondary, ...).
        Select all if key is None.
        """
        pass

    @abstractmethod
    def select2(self, key2):
        """secondary -> primary"""
        pass

    @abstractmethod
    def insert(self, key, values) -> None:
        pass

    @abstractmethod
    def update(self, key, values) -> None:
        pass

    @abstractmethod
    def delete(self, key) -> None:
        pass


class CSVIndexer(BaseIndexer):
    def __init__(self, index_file, cols):
        super().__init__(cols)
        self._index_file = index_file
        self._index = self._key_for_key2 = self._nr_index = None
        self._load()

    def _load(self) -> None:
        self._index = {}
        self._key_for_key2 = {}
        self._nr_index = 0
        # fid,path,version,format
        with open(self._index_file, 'r') as fi:
            reader = csv.reader(fi)
            line = -1
            for line, (fid, path, version, format) in enumerate(reader):
                fid, version = int(fid), int(version)
                self._index[fid] = (line, path, version, format)
                self._key_for_key2[path] = fid
            self._nr_index = line + 1

    def _create_fid(self) -> int:
        # TODO: Use inode number or UID instead of line number
        return self._nr_index

    def select(self, key=None) -> tuple:
        return self._index if key is None else self._index.get(key)
    
    def select2(self, key2):
        return self._key_for_key2.get(key2)

    def insert(self, fid: int = None, path: str = None,
                      version: int = 0, format: str = 'INI') -> int:
        fid = fid or self._create_fid()
        path = path or ''
        line = self._nr_index
        self._nr_index += 1
        self._index[fid] = (line, path, version, format)
        self._key_for_key2[path] = fid
        with open(self._index_file, 'a') as fo:
            writer = csv.writer(fo)
            writer.writerow((fid, path, version, format))
        return fid

    def update(self, fid: int, path: str = None, version: int = 0) -> None:
        _line, _path, _version, _format = self._index[fid]
        _path = path or _path
        _version = version(_version) if isinstance(version, collections.abc.Callable) else version
        self._index[fid] = (_line, _path, _version, _format)
        # TODO: Replace one line of an index file instead of full reflushing
        with open(self._index_file, 'w') as fo:
            writer = csv.writer(fo)
            for fid, (line, path, version, format) in sorted(
                    self._index.items(), key=lambda kv: kv[1][0]):
                writer.writerow((fid, path, version, format))

    def delete(self, fid: None) -> None:
        # TODO
        raise NotImplementedError()
        _, path, _, _ = self._index[fid]
        del self._index[fid]
        del self._key_for_key2[path]


class SQLIndexer(BaseIndexer):
    def __init__(self, conn: SQLConnection, table: str, cols: tuple):
        super().__init__(cols)
        self._table = table
        self._conn = conn

    def select(self, key=None) -> tuple:
        with self._conn.cursor() as cursor:
            if key is None:
                cursor.execute(f'SELECT * FROM {self._table}')
                ret = cursor.fetchall()
            else:
                cursor.execute(f'SELECT * FROM {self._table} WHERE {self._primary}=%s', (key,))
                ret = cursor.fetchone()
        return ret
        
    def select2(self, key2):
        with self._conn.cursor() as cursor:
            cursor.execute(f'SELECT {self._primary} FROM {self._table} WHERE {self._secondary}=%s', (key2,))
            ret = cursor.fetchone()
        if ret is None:
            return None
        return ret[0]

    def insert(self, **cols) -> int:
        key = cols.get(self._primary)
        fields = tuple(f for f in cols if cols[f] is not None)
        values = tuple(cols[f] for f in fields)
        with self._conn.cursor() as cursor:
            fmt_fields = ', '.join(fields)
            fmt_values = ', '.join(('%s',) * len(values))
            cursor.execute(
                f'INSERT INTO {self._table} ({fmt_fields}) VALUES ({fmt_values})',
                values)
            if key is None:  # for AUTO_INCREMENT primary key
                cursor.execute(f'SELECT LAST_INSERT_ID()')
                key = cursor.fetchone()[0]
        return key

    def update(self, key, **cols) -> None:
        with self._conn.transaction(isolation_level='SERIALIZABLE') as cursor:
            fields = tuple(f for f in cols if cols[f] is not None)
            cursor.execute(
                f'SELECT {fields} FROM {self._table} WHERE {self._primary}=%s',
                (key,))
            ret = cursor.fetchone()
            if not ret:
                raise KeyError(f'Key {self._primary}={key} not found')
            values = []
            for field, val in zip(fields, ret):
                if isinstance(cols[field], collections.abc.Callable):
                    new_val = cols[field](val)  # TODO: this can be reduced to `version = version + 1`
                else:
                    new_val = val
                values.append(new_val)
            assignments = ', '.join([f'{f} = %s' for f in fields])
            cursor.execute(
                f'UPDATE {self._table} SET {assignments} WHERE {self._primary}=%s',
                tuple(values) + (key,))