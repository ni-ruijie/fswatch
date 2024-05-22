import macros
from abc import abstractmethod
from math import modf
from datetime import datetime
from time import time
from typing import Callable
from threading import Thread, Lock, Semaphore, Event
if macros.LIB_SQL == 'mysql.connector':
    import mysql.connector as libsql
    from mysql.connector.connection import MySQLConnection
    from mysql.connector.connection import MySQLCursor
    from mysql.connector.pooling import MySQLConnectionPool
elif macros.LIB_SQL == 'pymysql':
    import pymysql as libsql
    from pymysql.connections import Connection as MySQLConnection
    from pymysql.cursors import Cursor as MySQLCursor
    from database.pymysqlpool import ConnectionPool as MySQLConnectionPool
from queue import Queue
import os
from event import InotifyEvent, ExtendedEvent
from loguru import logger
import settings


def _dbconfig():
    return dict(
        host=settings.db_host,
        user=settings.db_user,
        password=settings.db_password,
        database=settings.db_database
    )


class CursorContext:
    def __init__(self, conn: MySQLConnection) -> None:
        self._conn = conn
        self._cursor = None

    def __enter__ (self) -> MySQLCursor:
        self._cursor = self._conn.cursor()
        return self._cursor
    
    def __exit__(self, exc_type, exc_value, exc_tb) -> None:
        self._cursor.close()
        if exc_type is not None:
            logger.warning(f'{self.__class__.__name__}: Suppress {exc_type.__name__} "{exc_value}" and rollback.')
            self._conn.rollback()
            return True
        else:
            self._conn.commit()  # NOTE: why commit even outside transaction?


class TransactionContext(CursorContext):
    def __init__(self, conn: MySQLConnection, *args, **kwargs) -> None:
        super().__init__(conn)
        self._args = args
        if 'retry' in kwargs:
            self._retry = kwargs.pop('retry')
        self._kwargs = kwargs

    if macros.LIB_SQL == 'mysql.connector':
        def __enter__(self) -> MySQLCursor:
            self._conn.start_transaction(*self._args, **self._kwargs)
            return super().__enter__()
    elif macros.LIB_SQL == 'pymysql':
        def __enter__(self) -> MySQLCursor:
            self._conn.begin()  # isolation level ignored
            return super().__enter__()
    
    def __exit__(self, exc_type, exc_value, exc_tb) -> None:
        super().__exit__(exc_type, exc_value, exc_tb)


class ConnectionContext:
    def __init__(self, pool: MySQLConnectionPool,
                 sem: Semaphore, sub_ctx: CursorContext) -> None:
        self._pool = pool
        self._sem = sem
        self._sub_ctx: CursorContext = sub_ctx
        self._conn = None
    
    def __enter__(self) -> MySQLCursor:
        self._sem.__enter__()
        self._sub_ctx._conn = self._conn = self._pool.get_connection()
        return self._sub_ctx.__enter__()

    def __exit__(self, exc_type, exc_value, exc_tb) -> None:
        self._sub_ctx.__exit__(exc_type, exc_value, exc_tb)
        self._conn.close()
        self._sem.__exit__(exc_type, exc_value, exc_tb)


class ExLockContext:
    def __init__(self, cursor_func: Callable, name: str) -> None:
        self._cursor_func = cursor_func
        self._name = name

    def __enter__(self) -> None:
        with self._cursor_func() as cursor:
            cursor.execute('SELECT GET_LOCK(%s, -1)', (self._name,))
            cursor.fetchall()

    def __exit__(self, exc_type, exc_value, exc_tb) -> None:
        with self._cursor_func() as cursor:
            cursor.execute('SELECT RELEASE_LOCK(%s)', (self._name,))
            cursor.fetchall()


class ConnectionSingleton:
    def __init__(self):
        self._resource = None
        self._lock = Lock()
        self._pid = os.getpid()
        self._enabled = settings.db_enabled

    @property
    def enabled(self) -> bool:
        return self._enabled
    
    @property
    def initialized(self) -> bool:
        return self._resource is not None

    def init_conn(self) -> None:
        if not self._enabled:
            return
        if self._resource is None:
            with self._lock:
                if self._resource is None:
                    self._resource = self._init_resource()

    @abstractmethod
    def _init_resource(self):
        pass

    def lazy_init(func):
        def inner(self: 'ConnectionSingleton', *args, **kwargs):
            self.init_conn()
            return func(self, *args, **kwargs)
        return inner


class SQLConnection(ConnectionSingleton):
    def __init__(self):
        super().__init__()

    @property
    def _conn(self) -> MySQLConnection:
        return self._resource

    def _init_resource(self):
        res = libsql.connect(**_dbconfig())
        logger.success(f'{self}: Connection established.')
        return res
    
    @ConnectionSingleton.lazy_init
    def cursor(self, *args, **kwargs) -> CursorContext:
        return CursorContext(self._conn, *args, **kwargs)

    @ConnectionSingleton.lazy_init
    def transaction(self, *args, **kwargs) -> TransactionContext:
        return TransactionContext(self._conn, *args, **kwargs)
    
    def close_conn(self) -> None:
        if self._conn is not None:
            self._conn.close()


class SQLConnectionPool(SQLConnection):
    def __init__(self, pool_size=8):
        super().__init__()
        self._pool_size = pool_size
        self._sem = Semaphore(pool_size)

    @property
    def _pool(self) -> MySQLConnectionPool:
        return self._resource

    def _init_resource(self):
        res = MySQLConnectionPool(
            pool_size=self._pool_size,
            **_dbconfig()
        )
        return res
    
    @ConnectionSingleton.lazy_init
    def cursor(self, *args, **kwargs) -> ConnectionContext:
        return ConnectionContext(
            self._pool, self._sem,
            CursorContext(None, *args, **kwargs)
        )
    
    @ConnectionSingleton.lazy_init
    def transaction(self, *args, **kwargs) -> ConnectionContext:
        return ConnectionContext(
            self._pool, self._sem,
            TransactionContext(None, *args, **kwargs)
        )
    
    @ConnectionSingleton.lazy_init
    def lock(self, name: str) -> ExLockContext:
        return ExLockContext(self.cursor, name)


class ConnectionThread(Thread):
    def __init__(self, group=None, target: Callable[..., object] = None, name: str = None,
                 args=(), kwargs=None, *, daemon: bool = None) -> None:
        self._conn = SQLConnection()
        super().__init__(group, target, name, args,
                         {'conn': self._conn, **kwargs}, daemon=daemon)
        
    def run(self) -> None:
        self._conn.init_conn()
        super().run()
        self._conn.close_conn()


class SQLEventLogger(Thread, SQLConnection):
    def __init__(self):
        Thread.__init__(self)
        SQLConnection.__init__(self)
        self._ndigits_microsec = 6
        self._ndigits_uid = 4
        self._max_retry = 3
        if not self.enabled:
            logger.warning('SQL is not enabled. Events will not be recorded in database.')
        self._queue = Queue()
        self._stopped_event = Event()

    def start(self):
        if self.enabled:
            Thread.start(self)

    def run(self) -> None:
        while not self._stopped_event.is_set():
            event = self._queue.get()
            if event is None:
                continue
            for _ in range(self._max_retry+1):
                try:
                    self._log_event(event)
                    break
                except:
                    pass
            else:
                logger.warning(f'Cannot record {event} into table logs. Max retry exceeds')
                try:
                    self._log_event(event, direct_to_aux=True)
                except Exception as e:
                    logger.error(f'Cannot record {repr(event)} into table aux_logs either: '
                                    f'{e.__class__.__name__} "{e}"')
            if macros.TEST_SQL_DELAY:
                elapsed = time() - event._time
                logger.trace(f'SQL delayed {elapsed} secs')

    def _timestamp_to_decimal(self, timestamp):
        microsec, sec = modf(timestamp)
        microsec, sec = int(microsec * 10**self._ndigits_microsec), int(sec)
        return microsec, sec
    
    def log_event(self, event: InotifyEvent):
        if not self.enabled:
            return
        self._queue.put(event)
    
    @ConnectionSingleton.lazy_init
    def _log_event(self, event: InotifyEvent, direct_to_aux: bool = False) -> None:
        microsec, sec = self._timestamp_to_decimal(event._time)

        with self.transaction() as cursor:
            inc_id = 0
            if not direct_to_aux:
                cursor.execute(
                    'SELECT unique_time FROM logs '
                    'WHERE unique_time >= %s AND unique_time < %s '
                    'ORDER BY unique_time DESC LIMIT 1',
                    (f'{sec}.{microsec}', f'{sec}.{microsec+1}'))
                ret = cursor.fetchone()
                if ret is not None:
                    latest, = ret
                    inc_id = int(str(latest)[-self._ndigits_uid:]) + 1

            if direct_to_aux or inc_id == 10**self._ndigits_uid:
                # In case we run out of 10000 uids within one microsecond
                # NOTE: This occasion is rarely encountered
                cursor.execute(
                    'INSERT INTO aux_logs (time, mask, src_path, dest_path, monitor_pid)'
                    'VALUES (%s, %s, %s, %s, %s)',
                    (f'{sec}.{microsec}', event._mask, event._src_path, event._dest_path, self._pid))
            else:
                cursor.execute(
                    'INSERT INTO logs (unique_time, mask, src_path, dest_path, monitor_pid)'
                    'VALUES (%s, %s, %s, %s, %s)',
                    (f'{sec}.{microsec}{inc_id:0{self._ndigits_uid}d}', event._mask, event._src_path, event._dest_path, self._pid))
                
    @ConnectionSingleton.lazy_init
    def query_event(self, from_time: datetime = None, to_time: datetime = None,
                    pattern: str = None, mask: int = None, pid: int = None) -> tuple:
        # NOTE: `pattern` is a SQL pattern
        conditions = []
        if from_time:
            microsec, sec = self._timestamp_to_decimal(from_time.timestamp())
            conditions.append(f'unique_time >= {sec}.{microsec}')
        if to_time:
            microsec, sec = self._timestamp_to_decimal(to_time.timestamp())
            conditions.append(f'unique_time < {sec}.{microsec}')
        if pattern:
            conditions.append(f"(src_path LIKE '{pattern}' OR dest_path LIKE '{pattern}')")
        if mask:
            conditions.append(f'(mask & {mask} > 0)')
        if pid:
            conditions.append(f'monitor_pid = {pid}')
        conditions = 'WHERE ' + ' AND '.join(conditions) if conditions else ''
        events = []
        with self.cursor() as cursor:
            cursor.execute(
                f'SELECT unique_time, mask, src_path, dest_path, monitor_pid FROM logs {conditions}')
            ret = cursor.fetchall()
            for unique_time, mask, src_path, dest_path, monitor_pid in ret:
                if isinstance(mask, bytes):
                    mask = int.from_bytes(mask, 'big')
                events.append(ExtendedEvent(
                    mask, src_path, dest_path, datetime.fromtimestamp(float(unique_time))))
            # TODO: (Optional) check aux_logs
        return events

    def stop(self):
        self.close_conn()
        self._queue.put(None)
        self._stopped_event.set()
