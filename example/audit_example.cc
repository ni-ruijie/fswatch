#include <asm/types.h>
#include <sys/socket.h>
#include <linux/netlink.h>

#include <libaudit.h>
#include <iostream>
#include <iomanip>
#include <errno.h>
#include <string.h>
#include <sstream>

using namespace std;


void print_error(int, string);
void print_rule(struct audit_rule_data *);
string ohex(int);


void print_error(int ret, string tag) {
    char buffer[256];
    char *msg = strerror_r(errno, buffer, 256);
    cout << tag << " (" << ret << ") " << msg << endl;
}

void print_rule(struct audit_rule_data *rule) {
    cout << rule->flags << ' ' << rule->action << ' ' << rule->field_count << ' ' <<
        ohex(rule->mask[0]) << rule->fields[0] << ' ' << rule->values[0] << ' ' <<
        ohex(rule->fieldflags[0]) << ' ' << rule->buflen << endl;
}

string ohex(int x) {
    stringstream ss;
    ss << hex << x;
    return ss.str();
}


void monitoring(struct ev_loop *loop, struct ev_io *io, int revents);


int main() {
    // int netlink_socket = socket(AF_NETLINK, SOCK_RAW, NETLINK_AUDIT);
    // cout << "netlink: " << netlink_socket << endl;

    int ret;
    int fd = audit_open();
    cout << "fd: " << fd << endl;

    ret = audit_set_enabled(fd, 1);
    print_error(ret, "enable audit");

    struct audit_rule_data *rule = new audit_rule_data();
    print_rule(rule);

    // ret = audit_rule_syscallbyname_data(rule, "open");
    // print_error(ret, "rule syscall");
    // ret = audit_rule_syscallbyname_data(rule, "close");
    // print_error(ret, "rule syscall");
    ret = audit_rule_syscallbyname_data(rule, "mkdir");
    print_error(ret, "rule syscall");
    print_rule(rule);

    ret = audit_add_watch_dir(AUDIT_DIR, &rule, "/home/user/test/watched");
    print_error(ret, "add watch");
    print_rule(rule);

    // ret = audit_delete_rule_data(fd, rule, AUDIT_FILTER_EXIT, AUDIT_ALWAYS);
    // print_error(ret, "delete rule");
    ret = audit_delete_rule_data(fd, rule, AUDIT_FILTER_EXIT, AUDIT_ALWAYS);
    ret = audit_add_rule_data(fd, rule, AUDIT_FILTER_EXIT, AUDIT_ALWAYS);
    print_error(ret, "add rule");

    while (true) {
        struct audit_reply reply;
        int ret = audit_get_reply(fd, &reply, GET_REPLY_BLOCKING, 0);
        print_error(ret, "get reply");

        if (reply.type != AUDIT_EOE &&
            reply.type != AUDIT_PROCTITLE &&
            reply.type != AUDIT_PATH) {
            char *buf = new char[MAX_AUDIT_MESSAGE_LENGTH];
            snprintf(
                buf,
                MAX_AUDIT_MESSAGE_LENGTH,
                "Type=%s Message=%.*s",
                audit_msg_type_to_name(reply.type),
                reply.len,
                reply.message
            );
        }
        break;
    }

    ret = audit_delete_rule_data(fd, rule, AUDIT_FILTER_EXIT, AUDIT_ALWAYS);
    print_error(ret, "delete rule");

    audit_close(fd);
    
    return 0;
}