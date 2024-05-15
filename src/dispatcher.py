# Stub classes and functions for dispatching messages

import settings
from typing import Iterable
import re
import os
from functools import reduce
from typing import List, Tuple, Iterator
import json
from event import ExtendedInotifyConstants
from threading import Lock
import settings
from loguru import logger


class Route:
    def __init__(self, tag: str, pattern: re.Pattern, event: int, format: str):
        self.tag = tag
        self.pattern = pattern
        self.event = event
        self.format = format

    @classmethod
    def parse_routes(cls) -> Iterator['Route']:
        for tag, pattern, event, format in zip(
                settings.route_tags, settings.route_patterns, settings.route_events, settings.route_formats):
            pattern = re.compile(os.fsencode(pattern))
            event = reduce(
                lambda x, y: x | y,
                [getattr(ExtendedInotifyConstants, e) for e in event.split('|')]
            )
            yield Route(tag, pattern, event, format)


class BaseDispatcher:
    def __init__(self) -> None:
        self.routes = list(Route.parse_routes())
    
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


class RedisDispatcher(BaseDispatcher):
    def __init__(self) -> None:
        global notify_redis_store
        super().__init__()
        import sys
        for lib in settings.external_libs:
            sys.path.append(lib)
            logger.info(f'Set PATH=$PATH:{lib}')
        import notify_redis_store
        self._alert = notify_redis_store.NotifyRedisStore()
        self._default_group = settings.route_default_group
        self._groups = settings.route_groups

        self._lock = Lock()

    def gen_data_msg(self, tag: str = 'logs', group: str = '',
                     title: str = '', msg: str = '') -> dict:
        groups = [group] if group else self._groups.get(tag, [self._default_group])
        return {"data": [notify_redis_store.gen_data_message(tag, group, title, msg) for group in groups]}
    
    def emit(self, data: dict) -> None:
        for d in data['data']:
            self._alert.add(json.dumps(d))


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
    dispatchers = {'redis': RedisDispatcher, 'local': LocalDispatcher, 'rabbitmq': RabbitDispatcher}
    return dispatchers[settings.dispatcher_type](*args, **kwargs)
