import os

def get_inotify_info():
    fields = {field: None for field in ('max_queued_events', 'max_user_instances', 'max_user_watches')}
    for field in fields:
        with open(os.path.join('/proc/sys/fs/inotify', field), 'r') as fi:
            fields[field] = int(fi.read())
    return fields


class InotifyConstants:
    # The
    #   following bits can be specified in mask when calling
    #   inotify_add_watch(2) and may be returned in the mask field
    #   returned by read(2):
    IN_ACCESS = 0x00000001
    IN_ATTRIB = 0x00000004
    IN_CLOSE_WRITE = 0x00000008
    IN_CLOSE_NOWRITE = 0x00000010
    IN_CREATE = 0x00000100
    IN_DELETE = 0x00000200
    IN_DELETE_SELF = 0x00000400
    IN_MODIFY = 0x00000002
    IN_MOVE_SELF = 0x00000800
    IN_MOVED_FROM = 0x00000040
    IN_MOVED_TO = 0x00000080
    IN_OPEN = 0x00000020

    # The IN_ALL_EVENTS macro is defined as a bit mask of all of the
    #   above events.
    IN_ALL_EVENTS = 0x00000fff

    # Two additional convenience macros are defined:
    IN_MOVE = 0x000000c0
    IN_CLOSE = 0x00000018

    # The following further bits can be specified in mask when calling
    #   inotify_add_watch(2)
    IN_DONT_FOLLOW = 0x02000000
    IN_EXCL_UNLINK = 0x04000000
    IN_MASK_ADD = 0x20000000
    IN_ONESHOT = 0x80000000
    IN_ONLYDIR = 0x01000000
    IN_MASK_CREATE = 0x10000000

    # The following bits may be set in the mask field returned by
    #   read(2):
    IN_IGNORED = 0x00008000
    IN_ISDIR = 0x40000000
    IN_Q_OVERFLOW = 0x00004000
    IN_UNMOUNT = 0x00002000