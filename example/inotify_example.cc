#include <sys/inotify.h>
#include <iostream>
#include <iomanip>
#include <vector>

using namespace std;

static constexpr char names[][20] = {
    "IN_ACCESS",
    "IN_ATTRIB", 
    "IN_CLOSE_WRITE", 
    "IN_CLOSE_NOWRITE", 
    "IN_CREATE", 
    "IN_DELETE", 
    "IN_DELETE_SELF", 
    "IN_MODIFY", 
    "IN_MOVE_SELF", 
    "IN_MOVED_FROM", 
    "IN_MOVED_TO", 
    "IN_OPEN",
    "IN_ALL_EVENTS",
    "IN_MOVE",
    "IN_CLOSE",
    "IN_DONT_FOLLOW",
    "IN_EXCL_UNLINK",
    "IN_MASK_ADD",
    "IN_ONESHOT",
    "IN_ONLYDIR",
    "IN_MASK_CREATE",
    "IN_IGNORED",
    "IN_ISDIR",
    "IN_Q_OVERFLOW",
    "IN_UNMOUNT"
};

static constexpr uint32_t masks[] = {
    IN_ACCESS, 
    IN_ATTRIB, 
    IN_CLOSE_WRITE, 
    IN_CLOSE_NOWRITE, 
    IN_CREATE, 
    IN_DELETE, 
    IN_DELETE_SELF, 
    IN_MODIFY, 
    IN_MOVE_SELF, 
    IN_MOVED_FROM, 
    IN_MOVED_TO, 
    IN_OPEN,
    IN_ALL_EVENTS,
    IN_MOVE,
    IN_CLOSE,
    IN_DONT_FOLLOW,
    IN_EXCL_UNLINK,
    IN_MASK_ADD,
    IN_ONESHOT,
    IN_ONLYDIR,
    IN_MASK_CREATE,
    IN_IGNORED,
    IN_ISDIR,
    IN_Q_OVERFLOW,
    IN_UNMOUNT
};

int main() {
    for (int i = 0; i < sizeof(masks)/sizeof(masks[0]); ++i) {
        cout << names[i] << " = 0x" << setfill('0') << setw(8) << hex << masks[i] << endl;
    }
    return 0;
}