import os

def get_inotify_info():
    fields = {field: None for field in ('max_queued_events', 'max_user_instances', 'max_user_watches')}
    for field in fields:
        with open(os.path.join('/proc/sys/fs/inotify', field), 'r') as fi:
            fields[field] = int(fi.read())
    return fields
