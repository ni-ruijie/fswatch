import os
import os.path as osp
import json
from linux import InotifyConstants


def load_json(path: str) -> dict:
    with open(path, 'r') as fi:
        return json.load(fi)

def save_json(obj: dict, path: str) -> None:
    with open(path, 'w') as fo:
        json.dump(obj, fo)


class ExtendedInotifyConstants(InotifyConstants):
    EX_META = 0x100000000
    EX_RENAME = 0x200000000