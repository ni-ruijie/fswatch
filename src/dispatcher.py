# Stub classes and functions for dispatching messages,
# currently implemented as a rabbitmq
import pika


class BaseDispatcher:
    def __init__(self) -> None:
        pass
    
    def emit(self, data: dict) -> None:
        pass

    def gen_data_msg(self, tag: str = '', group: str = '',
                     title: str = '', msg: str = '') -> dict:
        pass

    def close(self) -> None:
        pass


class Dispatcher(BaseDispatcher):
    def __init__(self) -> None:
        self._connection = pika.BlockingConnection(
            pika.ConnectionParameters(host='localhost', heartbeat=0))
        self._channel = self._connection.channel()

        self._channel.exchange_declare(exchange='logs', exchange_type='fanout')

    def emit(self, data: dict) -> None:
        self._channel.basic_publish(exchange='logs', routing_key='', body=data['msg'])

    def gen_data_msg(self, tag: str = '', group: str = '',
                     title: str = '', msg: str = '') -> dict:
        return dict(
            tag=tag,
            group=group,
            title=title,
            msg=msg
        )
    
    def close(self) -> None:
        self._channel.close()
