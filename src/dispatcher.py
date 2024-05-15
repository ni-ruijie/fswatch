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
from scheduler import HistogramScheduler, ProxyScheduler


_name_to_scheduler = {
    '': ProxyScheduler, 'direct': ProxyScheduler, 'proxy': ProxyScheduler,
    'hist': HistogramScheduler, 'histogram': HistogramScheduler
}


class Route:
    def __init__(self, tag: str, pattern: re.Pattern, event: int, format: str,
                 scheduler) -> None:
        self.tag = tag
        self.title = ''
        self.description = ''
        self.pattern = pattern
        self.event = event
        self.format = format
        self.scheduler = scheduler
        scheduler.route = self

    @classmethod
    def parse_routes(cls, callback) -> Iterator['Route']:
        for tag, pattern, event, format, scheduler in zip(
                settings.route_tags, settings.route_patterns,
                settings.route_events, settings.route_formats, settings.route_schedulers):
            pattern = re.compile(os.fsencode(pattern))
            event = reduce(
                lambda x, y: x | y,
                [getattr(ExtendedInotifyConstants, e) for e in event.split('|')]
            )
            scheduler = scheduler.split(' ')
            scheduler, args = scheduler[0], scheduler[1:]
            args = [int(arg) for arg in args]
            print(scheduler, args)
            scheduler = _name_to_scheduler[scheduler](
                callback, *args)
            logger.info(f'Using scheduler {scheduler} for route {tag}')
            yield Route(tag, pattern, event, format, scheduler)


class BaseDispatcher:
    def __init__(self) -> None:
        self.routes = list(Route.parse_routes(self._emit))
        for route in self.routes:
            route.scheduler.start()
    
    def emit(self, route: Route, **data) -> None:
        route.scheduler.put(data)
    
    def _emit(self, route: Route, data: dict) -> None:
        pass

    def close(self) -> None:
        for route in self.routes:
            route.scheduler.stop()


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
    
    def _emit(self, route: Route, data: dict) -> None:
        tag, title, msg = route.tag, route.title, route.format.format(**data)
        for group in self._groups.get(tag, [self._default_group]):
            d = notify_redis_store.gen_data_message(tag, group, title, msg)
            self._alert.add(json.dumps(d))


class LocalDispatcher(BaseDispatcher):
    def __init__(self) -> None:
        super().__init__()
        self._lock = Lock()
        self._fs = {}
        for tag in settings.route_tags:
            self._fs[tag] = open(f'.fswatch.{tag}.buf', 'ab')

    def _emit(self, route: Route, data: dict) -> None:
        tag, title, msg = route.tag, route.title, route.format.format(**data)
        f = self._fs[tag]
        with self._lock:
            f.write((msg + '\n').encode())
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

    def _emit(self, route: Route, data: dict) -> None:
        tag, title, msg = route.tag, route.title, route.format.format(**data)
        self._channel.basic_publish(exchange='logs', routing_key='', body=msg)
    
    def close(self) -> None:
        self._channel.close()


def Dispatcher(*args, **kwargs):
    dispatchers = {'redis': RedisDispatcher, 'local': LocalDispatcher, 'rabbitmq': RabbitDispatcher}
    return dispatchers[settings.dispatcher_type](*args, **kwargs)
