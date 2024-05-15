import os
import os.path as osp
import json
import string
from tabulate import tabulate


def load_json(path: str) -> dict:
    with open(path, 'r') as fi:
        return json.load(fi)

def save_json(obj: dict, path: str) -> None:
    with open(path, 'w') as fo:
        json.dump(obj, fo)

class Formatter(string.Formatter):
    def format_field(self, value, format_spec: str):
        if format_spec.endswith('!none'):
            if value is None:
                return ''
            format_spec = format_spec.rstrip('!none')
        if isinstance(value, dict) and format_spec == 'tab':
            try:  # TODO: deal with 2d table Dict[str, List]
                return tabulate([map(str, value.values())], headers=map(str, value.keys()))
            except:
                pass
        return super().format_field(value, format_spec)
    
_fmt = Formatter()

def format(s, **kwargs):
    return _fmt.format(s, **kwargs)
