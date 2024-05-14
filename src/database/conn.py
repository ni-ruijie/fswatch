from abc import abstractmethod
from math import modf
from datetime import datetime
from typing import Callable
from threading import Thread, Lock, Semaphore
import mysql.connector
import mysql.connector.pooling
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
    def __init__(self, conn: mysql.connector.connection.MySQLConnection) -> None:
        self._conn = conn
        self._cursor = None

    def __enter__ (self) -> mysql.connector.connection.MySQLCursor:
        self._cursor = self._conn.cursor()
        return self._cursor
    
    def __exit__(self, exc_type, exc_value, exc_tb) -> None:
        self._cursor.close()


class TransactionContext(CursorContext):
    def __init__(self, conn: mysql.connector.connection.MySQLConnection, *args, **kwargs) -> None:
        super().__init__(conn)
        self.args = args
        self.kwargs = kwargs

    def __enter__ (self) -> mysql.connector.connection.MySQLCursor:
        self._conn.start_transaction(*self.args, **self.kwargs)
        return super().__enter__()
    
    def __exit__(self, exc_type, exc_value, exc_tb) -> None:
        super().__exit__(exc_type, exc_value, exc_tb)
        self._conn.commit()


class ConnectionContext:
    def __init__(self, pool: mysql.connector.pooling.MySQLConnectionPool,
                 sem: Semaphore, sub_ctx: CursorContext) -> None:
        self._pool = pool
        self._sem = sem
        self._sub_ctx: CursorContext = sub_ctx
        self._conn = None
    
    def __enter__(self) -> mysql.connector.connection.MySQLCursor:
        self._sem.__enter__()
        self._sub_ctx._conn = self._conn = self._pool.get_connection()
        return self._sub_ctx.__enter__()

    def __exit__(self, exc_type, exc_value, exc_tb) -> None:
        self._sub_ctx.__exit__(exc_type, exc_value, exc_tb)
        self._conn.close()
        self._sem.__exit__(exc_type, exc_value, exc_tb)


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
    def _conn(self) -> mysql.connector.connection.MySQLConnection:
        return self._resource

    def _init_resource(self):
        res = mysql.connector.connect(**_dbconfig())
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
    def _pool(self) -> mysql.connector.pooling.MySQLConnectionPool:
        return self._resource

    def _init_resource(self):
        res = mysql.connector.pooling.MySQLConnectionPool(
            pool_size=self._pool_size,
            **_dbconfig()
        )
        return res
    
    @ConnectionSingleton.lazy_init
    def cursor(self, *args, **kwargs) -> CursorContext:
        return ConnectionContext(
            self._pool, self._sem,
            CursorContext(None, *args, **kwargs)
        )
    
    @ConnectionSingleton.lazy_init
    def transaction(self, *args, **kwargs) -> TransactionContext:
        return ConnectionContext(
            self._pool, self._sem,
            TransactionContext(None, *args, **kwargs)
        )


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


class SQLEventLogger(SQLConnection):
    def __init__(self):
        super().__init__()
        self._ndigits_microsec = 6
        self._ndigits_uid = 4
        if not self.enabled:
            logger.warning('SQL is not enabled. Events will not be recorded in database.')

    def _timestamp_to_decimal(self, timestamp):
        microsec, sec = modf(timestamp)
        microsec, sec = int(microsec * 10**self._ndigits_microsec), int(sec)
        return microsec, sec
    
    def log_event(self, event: InotifyEvent):
        if not self.enabled:
            return
        microsec, sec = self._timestamp_to_decimal(event._time)

        with self.transaction(isolation_level='SERIALIZABLE') as cursor:
            cursor.execute(
                'SELECT unique_time FROM logs '
                'WHERE unique_time >= %s AND unique_time < %s '
                'ORDER BY unique_time DESC LIMIT 1',
                (f'{sec}.{microsec}', f'{sec}.{microsec+1}'))
            ret = cursor.fetchone()
            if ret is not None:
                latest, = ret
                inc_id = int(str(latest)[-self._ndigits_uid:]) + 1
            else:
                inc_id = 0
            if inc_id == 10**self._ndigits_uid:
                # In case we run out of 10000 uids within one microsecond
                # NOTE: This occasion is rarely encountered
                self._conn.commit()  # commit the previous transaction
                cursor.execute(
                    'INSERT INTO aux_logs (time, mask, src_path, dest_path, monitor_pid)'
                    'VALUES (%s, %s, %s, %s, %s)',
                    (f'{sec}.{microsec}', event._mask, event._src_path, event._dest_path, self._pid))
            else:
                cursor.execute(
                    'INSERT INTO logs (unique_time, mask, src_path, dest_path, monitor_pid)'
                    'VALUES (%s, %s, %s, %s, %s)',
                    (f'{sec}.{microsec}{inc_id}', event._mask, event._src_path, event._dest_path, self._pid))
                
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
            conditions.append(f'pid = {pid}')
        conditions = 'WHERE ' + ' AND '.join(conditions) if conditions else ''
        events = []
        with self.cursor() as cursor:
            cursor.execute(
                f'SELECT unique_time, mask, src_path, dest_path, monitor_pid FROM logs {conditions}')
            ret = cursor.fetchall()
            for unique_time, mask, src_path, dest_path, monitor_pid in ret:
                events.append(ExtendedEvent(
                    mask, src_path, dest_path, datetime.fromtimestamp(float(unique_time))))
            # TODO: (Optional) check aux_logs
        return events


dbconn = SQLConnection()  # NOTE: for main thread only!
