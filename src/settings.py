class _T:
    """An option that contains multiple values"""
    def __init__(self, data, dtype=None, nargs='*'):
        self._data = data
        self.dtype = dtype or type(data[0])
        self.nargs = nargs

    @classmethod
    def default(cls, *args, dtype=None, nargs='*'):
        return cls(args, dtype=dtype, nargs=nargs)
    
    def __iter__(self):
        return iter(self._data)
    
    def __getitem__(self, index):
        return self._data[index]
    
    def __str__(self):
        return str(self._data)

# For monitor and all workers
worker_every_path = False  # if true, use one worker thread (along with an inotify instance) for each path
worker_extra_mask = ''  # record additional inotify events to database, or only record route_events if not set
worker_blocking_read = True  # blocking or non-blocking read from the inotify file descriptor

# For file tracking
tracker_cachedir = '.track'
tracker_patterns = (r'.*\.(ini|INI)', r'.*\.py')  # M tracking re patterns
tracker_filetypes = ('INI', 'GENERIC')  # M corresponding parser types
tracker_indexer = 'sql'  # choices: csv, sql

# For message routing
route_tags = ('logs', 'warnings', 'tracks')  # N destinations
route_patterns = (r'.*', r'.*', r'.*')  # N watching re patterns
route_events = ('IN_ALL_EVENTS', 'EX_META', 'EX_MODIFY_CONFIG')  # N watching events
# TODO: route_types = ('', '')  # N watching types ('d' or 'f' for dirs and files)
route_formats = ('Event {ev_name} on {ev_src}', 'Alert at {msg_time}: {msg}', 'Modified {ev_src}')  # N output formats
route_schedulers = ('direct', 'direct', 'direct')  # choices: direct, histogram
route_default_group = ''
route_groups = {}  # if `tag in route_groups`, send `tag` to that list of groups, otherwise send to default

# For controller
controller_basic_interval = 600  # seconds
controller_max_interval = 3600*24
controller_limit_threshold = 0.9

# For delay queue
buffer_queue_delay = 0.5

# For database
db_enabled = True
db_host = 'localhost'
db_user = 'root'
db_password = 'password'
db_database = 'fswatch_db'

# For debug only
external_libs = _T.default(dtype=str)
dispatcher_type = 'redis'