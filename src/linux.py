import ctypes
import ctypes.util


__all__ = [
    'libc', 'libaudit',
    'InotifyConstants', 'AuditConstants',
    'inotify_event_struct', 'audit_rule_data_struct',
    'inotify_init', 'inotify_add_watch', 'inotify_rm_watch',
    'audit_open', 'audit_add_rule_data', 'audit_add_watch_dir',
    'audit_get_reply', 'audit_delete_rule_data', 'audit_close'
]


# Defined in linux/inotify.h

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


class inotify_event_struct(ctypes.Structure):
    _fields_ = [
        ("wd", ctypes.c_int),
        ("mask", ctypes.c_uint32),
        ("cookie", ctypes.c_uint32),
        ("len", ctypes.c_uint32),
        ("name", ctypes.c_char_p),
    ]


# Defined in libc

libc = ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True)

inotify_init = libc.inotify_init
inotify_add_watch = libc.inotify_add_watch
inotify_rm_watch = libc.inotify_rm_watch


# Defined in linux/audit.h

class AuditConstants:
    AUDIT_MAX_FIELDS   = 64
    AUDIT_MAX_KEY_LEN  = 256
    AUDIT_BITMASK_SIZE = 64


class audit_rule_data_struct(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_uint32),
        ("action", ctypes.c_uint32),
        ("field_count", ctypes.c_uint32),
        ("mask", ctypes.c_uint32 * AuditConstants.AUDIT_BITMASK_SIZE),
        ("fields", ctypes.c_uint32 * AuditConstants.AUDIT_MAX_FIELDS),
        ("values", ctypes.c_uint32 * AuditConstants.AUDIT_MAX_FIELDS),
        ("fieldflags", ctypes.c_uint32 * AuditConstants.AUDIT_MAX_FIELDS),
        ("buflen", ctypes.c_uint32),
        ("buf", ctypes.c_char_p),
    ]


# sruct audit_reply {
# 	int                      type;
# 	int                      len;
# 	struct nlmsghdr         *nlh;
# 	struct audit_message     msg;

# 	/* Using a union to compress this structure since only one of
# 	 * the following should be valid for any packet. */
# 	union {
# 	struct audit_status     *status;
# 	struct audit_rule_data  *ruledata;
# 	struct audit_login      *login;
# 	char                    *message;
# 	struct nlmsgerr         *error;
# 	struct audit_sig_info   *signal_info;
# 	struct daemon_conf      *conf;
# #ifdef AUDIT_FEATURE_VERSION
# 	struct audit_features	*features;
# #endif
# 	};
# };


# Defined in libaudit

libaudit = ctypes.CDLL(ctypes.util.find_library('audit'))

audit_action_to_name = libaudit.audit_action_to_name
# audit_add_dir = libaudit.audit_add_dir
audit_add_rule_data = libaudit.audit_add_rule_data
audit_add_watch = libaudit.audit_add_watch
audit_add_watch_dir = libaudit.audit_add_watch_dir
audit_can_control = libaudit.audit_can_control
audit_can_read = libaudit.audit_can_read
audit_can_write = libaudit.audit_can_write
audit_close = libaudit.audit_close
audit_delete_rule_data = libaudit.audit_delete_rule_data
audit_detect_machine = libaudit.audit_detect_machine
audit_determine_machine = libaudit.audit_determine_machine
audit_elf_to_machine = libaudit.audit_elf_to_machine
audit_encode_nv_string = libaudit.audit_encode_nv_string
audit_encode_value = libaudit.audit_encode_value
audit_errno_to_name = libaudit.audit_errno_to_name
audit_field_to_name = libaudit.audit_field_to_name
audit_flag_to_name = libaudit.audit_flag_to_name
audit_fstype_to_name = libaudit.audit_fstype_to_name
audit_ftype_to_name = libaudit.audit_ftype_to_name
audit_get_features = libaudit.audit_get_features
audit_getloginuid = libaudit.audit_getloginuid
audit_get_reply = libaudit.audit_get_reply
audit_get_session = libaudit.audit_get_session
audit_is_enabled = libaudit.audit_is_enabled
audit_log_acct_message = libaudit.audit_log_acct_message
audit_log_semanage_message = libaudit.audit_log_semanage_message
audit_log_user_avc_message = libaudit.audit_log_user_avc_message
audit_log_user_command = libaudit.audit_log_user_command
audit_log_user_comm_message = libaudit.audit_log_user_comm_message
audit_log_user_message = libaudit.audit_log_user_message
audit_machine_to_elf = libaudit.audit_machine_to_elf
audit_machine_to_name = libaudit.audit_machine_to_name
audit_make_equivalent = libaudit.audit_make_equivalent
audit_msg = libaudit.audit_msg
audit_msg_type_to_name = libaudit.audit_msg_type_to_name
audit_name_to_action = libaudit.audit_name_to_action
audit_name_to_errno = libaudit.audit_name_to_errno
audit_name_to_field = libaudit.audit_name_to_field
audit_name_to_flag = libaudit.audit_name_to_flag
audit_name_to_fstype = libaudit.audit_name_to_fstype
audit_name_to_ftype = libaudit.audit_name_to_ftype
audit_name_to_machine = libaudit.audit_name_to_machine
audit_name_to_msg_type = libaudit.audit_name_to_msg_type
audit_name_to_syscall = libaudit.audit_name_to_syscall
audit_number_to_errmsg = libaudit.audit_number_to_errmsg
audit_open = libaudit.audit_open
audit_operator_to_symbol = libaudit.audit_operator_to_symbol
audit_request_features = libaudit.audit_request_features
audit_request_rules_list_data = libaudit.audit_request_rules_list_data
audit_request_signal_info = libaudit.audit_request_signal_info
audit_request_status = libaudit.audit_request_status
audit_reset_lost = libaudit.audit_reset_lost
audit_rule_fieldpair_data = libaudit.audit_rule_fieldpair_data
audit_rule_free_data = libaudit.audit_rule_free_data
audit_rule_interfield_comp_data = libaudit.audit_rule_interfield_comp_data
audit_rule_syscallbyname_data = libaudit.audit_rule_syscallbyname_data
audit_rule_syscall_data = libaudit.audit_rule_syscall_data
audit_send = libaudit.audit_send
audit_set_backlog_limit = libaudit.audit_set_backlog_limit
audit_set_backlog_wait_time = libaudit.audit_set_backlog_wait_time
audit_set_enabled = libaudit.audit_set_enabled
audit_set_failure = libaudit.audit_set_failure
audit_set_feature = libaudit.audit_set_feature
audit_setloginuid = libaudit.audit_setloginuid
audit_set_loginuid_immutable = libaudit.audit_set_loginuid_immutable
audit_set_pid = libaudit.audit_set_pid
audit_set_rate_limit = libaudit.audit_set_rate_limit
# audit_strsplit = libaudit.audit_strsplit
# audit_strsplit_r = libaudit.audit_strsplit_r
audit_syscall_to_name = libaudit.audit_syscall_to_name
audit_trim_subtrees = libaudit.audit_trim_subtrees
audit_update_watch_perms = libaudit.audit_update_watch_perms
audit_value_needs_encoding = libaudit.audit_value_needs_encoding
