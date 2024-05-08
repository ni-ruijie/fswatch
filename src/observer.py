# Dummy observers for debugging messages sent by the corresponding stub dispatcher.

from threading import Thread
import argparse
import os
from loguru import logger
import settings


class LocalObserver(Thread):
    def __init__(self):
        super().__init__()
        self._f = open('.fswatch.buf', 'rb')

        print('Waiting for logs. To exit press CTRL+C')
        
        self.start()

    def run(self):
        while True:
            line = self._f.readline()
            if line:
                logger.info(line.decode().rstrip('\n'))


class RabbitObserver:
    def __init__(self):
        import pika
        connection = pika.BlockingConnection(
            pika.ConnectionParameters(host='localhost', heartbeat=0))
        channel = connection.channel()

        channel.exchange_declare(exchange='logs', exchange_type='fanout')

        result = channel.queue_declare(queue='', exclusive=True)
        queue_name = result.method.queue

        channel.queue_bind(exchange='logs', queue=queue_name)

        print('Waiting for logs. To exit press CTRL+C')

        def callback(ch, method, properties, body):
            logger.info(body.decode())

        channel.basic_consume(
            queue=queue_name, on_message_callback=callback, auto_ack=True)

        channel.start_consuming()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-d', '--dispatcher_type', type=str, default=settings.dispatcher_type)
    args = parser.parse_args()
    observers = {'local': LocalObserver, 'rabbitmq': RabbitObserver}
    ob = observers[args.dispatcher_type]()
