# Dummy observers for debugging messages sent by the corresponding stub dispatcher.

from threading import Thread
import argparse
import os
from time import sleep
from loguru import logger
import settings


class LocalObserver(Thread):
    """Analog to `tail -f .fswatch.logs.buf`"""
    def __init__(self, tag):
        super().__init__()
        self._tag = tag
        self._f = open(f'.fswatch.{tag}.buf', 'rb')

        print(f'Waiting for {tag}. To exit press CTRL+C')

        self.start()

    def run(self):
        line = b''
        while True:
            line += self._f.readline()
            if line.endswith(b'\n'):
                logger.info(line.decode().rstrip('\n'))
                line = b''


class RabbitObserver:
    def __init__(self, tag):
        import pika
        connection = pika.BlockingConnection(
            pika.ConnectionParameters(host='localhost', heartbeat=0))
        channel = connection.channel()

        channel.exchange_declare(exchange=tag, exchange_type='fanout')

        result = channel.queue_declare(queue='', exclusive=True)
        queue_name = result.method.queue

        channel.queue_bind(exchange=tag, queue=queue_name)

        print(f'Waiting for {tag}. To exit press CTRL+C')

        def callback(ch, method, properties, body):
            logger.info(body.decode())

        channel.basic_consume(
            queue=queue_name, on_message_callback=callback, auto_ack=True)

        channel.start_consuming()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-n', '--tag', type=str, default='logs')
    parser.add_argument('-d', '--dispatcher_type', type=str, default=settings.dispatcher_type)
    args = parser.parse_args()
    observers = {'local': LocalObserver, 'rabbitmq': RabbitObserver}
    ob = observers[args.dispatcher_type](args.tag)
