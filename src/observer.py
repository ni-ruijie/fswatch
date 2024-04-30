import threading
import pika
import argparse
import os
from loguru import logger


def main(args):
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
    args = parser.parse_args()
    main(args)