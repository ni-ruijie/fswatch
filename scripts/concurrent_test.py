#!/usr/bin/python3
from threading import Thread
from time import sleep
import os


class Writer(Thread):
    def __init__(self, n, c):
        super().__init__()
        self._n = n
        self._c = c

    def run(self):
        with open(os.path.expanduser(self._n), 'w') as fo:
            fo.write(self._c)


def main(args):
    writers = []
    for i in range(args.num_files):
        writers.append(Writer(f'~/test/watched/f{i}.txt', f'{args.c}={i}'))
    for writer in writers:
        writer.start()
    for writer in writers:
        writer.join()

    writers = []
    for i in range(args.num_files)；
        writers.append(Writer(f'~/test/watched/f{i}.txt', f'{args.c}={i}\nb={i+1}'))
    for writer in writers:
        writer.start()
    for writer in writers:
        writer.join()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('num_files', type=int)
    parser.add_argument('c', type=str)
    args = parser.parse_args()
    main(args)

