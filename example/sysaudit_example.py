import sys
from time import sleep

def hook(event, args):
    print("Event:", event, args)

sys.addaudithook(hook)

with open('scripts/test1.sh', 'r'):
    pass

# sys.audit("event_name", 1, 2, dict(key="value"))