# Stub classes and functions for dispatching messages

import settings


class BaseDispatcher:
    def __init__(self) -> None:
        pass
    
    def emit(self, data: dict) -> None:
        pass

    def gen_data_msg(self, tag: str = '', group: str = '',
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
        self._f = open('.fswatch.buf', 'ab')

    def emit(self, data: dict) -> None:
        self._f.write((data['msg'] + '\n').encode())
        self._f.flush()

    def close(self) -> None:
        self._f.close()


class RabbitDispatcher(BaseDispatcher):
    def __init__(self) -> None:
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
