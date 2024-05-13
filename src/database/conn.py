from math import modf
from datetime import datetime
from threading import Lock
import mysql.connector
import mysql.connector.cursor_cext
import os
from event import InotifyEvent, ExtendedEvent
from loguru import logger
import settings


class CursorContext:
    def __init__(self, db: mysql.connector.connection_cext.CMySQLConnection) -> None:
        self._db = db
        self._cursor = None

    def __enter__ (self) -> mysql.connector.cursor_cext.CMySQLCursor:
        self._cursor = self._db.cursor()
        return self._cursor
    
    def __exit__(self, exc_type, exc_value, exc_tb) -> None:
        self._cursor.close()


class TransactionContext(CursorContext):
    def __init__(self, db: mysql.connector.connection_cext.CMySQLConnection, *args, **kwargs) -> None:
        super().__init__(db)
        self.args = args
        self.kwargs = kwargs

    def __enter__ (self) -> mysql.connector.cursor_cext.CMySQLCursor:
        self._db.start_transaction(*self.args, **self.kwargs)
        return super().__enter__()
    
    def __exit__(self, exc_type, exc_value, exc_tb) -> None:
        super().__exit__(exc_type, exc_value, exc_tb)
        self._db.commit()


class SQLConnection:
    def __init__(self):
        self._db: mysql.connector.connection_cext.CMySQLConnection = None
        self._lock = Lock()
        self._ndigits_microsec = 6
        self._ndigits_uid = 4
        self._pid = os.getpid()

    @property
    def enabled(self):
        return self._db is not None

    def init_conn(self):
        if settings.db_enabled and self._db is None:
            with self._lock:
                if self._db is None:
                    try:
                        self._db = mysql.connector.connect(
                            host=settings.db_host,
                            user=settings.db_user,
                            password=settings.db_password,
                            database=settings.db_database
                        )
                        logger.success('SQLConnection: Connection established.')
                    except Exception as e:
                        logger.error(e)

    def _timestamp_to_decimal(self, timestamp):
        microsec, sec = modf(timestamp)
        microsec, sec = int(microsec * 10**self._ndigits_microsec), int(sec)
        return microsec, sec

    def log_event(self, event: InotifyEvent):
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
                self._db.commit()  # commit the previous transaction
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
                
    def cursor(self, *args, **kwargs):
        return CursorContext(self._db, *args, **kwargs)

    def transaction(self, *args, **kwargs):
        return TransactionContext(self._db, *args, **kwargs)


dbconn = SQLConnection()
dbconn.init_conn()
