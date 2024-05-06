import os
import os.path as osp
import json

def get_inotify_info() -> dict:
    fields = {field: None for field in ('max_queued_events', 'max_user_instances', 'max_user_watches')}
    for field in fields:
        with open(os.path.join('/proc/sys/fs/inotify', field), 'r') as fi:
            fields[field] = int(fi.read())
    return fields

def load_json(path: str) -> dict:
    with open(path, 'r') as fi:
        return json.load(fi)

def save_json(obj: dict, path: str) -> None:
    with open(path, 'w') as fo:
        json.dump(obj, fo)
