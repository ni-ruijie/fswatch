# Stub classes and functions for dispatching messages

import settings
from typing import Iterable
import re
import os
from functools import reduce
from typing import List, Tuple, Iterator
from event import ExtendedInotifyConstants
from threading import Lock
import settings


def parse_routes() -> Iterator[Tuple[str, re.Pattern, int]]:
    for tag, pattern, event in zip(
            settings.route_tags, settings.route_patterns, settings.route_events):
        pattern = re.compile(os.fsencode(pattern))
        event = reduce(
            lambda x, y: x | y,
            [getattr(ExtendedInotifyConstants, e) for e in event.split('|')]
        )
        yield (tag, pattern, event)


class BaseDispatcher:
    def __init__(self) -> None:
        self.routes = list(parse_routes())
    
    def emit(self, data: dict) -> None:
        pass

    def gen_data_msg(self, tag: str = 'logs', group: str = '',
                     title: str = '', msg: str = '') -> dict:
        return dict(
            tag=tag,
            group=group,
            title=title,
            msg=msg
        )

    def close(self) -> None:
        pass


class LocalDispatcher(BaseDispatcher):
    def __init__(self) -> None:
        super().__init__()
        self._lock = Lock()
        self._fs = {}
        for tag in settings.route_tags:
            self._fs[tag] = open(f'.fswatch.{tag}.buf', 'ab')

    def emit(self, data: dict) -> None:
        f = self._fs[data['tag']]
        with self._lock:
            f.write((data['msg'] + '\n').encode())
            f.flush()

    def close(self) -> None:
        for f in self._fs.values():
            f.close()


class RabbitDispatcher(BaseDispatcher):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__()
        import pika
        self._connection = pika.BlockingConnection(
            pika.ConnectionParameters(host='localhost', heartbeat=0))
        self._channel = self._connection.channel()

        self._channel.exchange_declare(exchange='logs', exchange_type='fanout')

    def emit(self, data: dict) -> None:
        self._channel.basic_publish(exchange='logs', routing_key='', body=data['msg'])
    
    def close(self) -> None:
        self._channel.close()


def Dispatcher(*args, **kwargs):
    dispatchers = {'local': LocalDispatcher, 'rabbitmq': RabbitDispatcher}
    return dispatchers[settings.dispatcher_type](*args, **kwargs)
