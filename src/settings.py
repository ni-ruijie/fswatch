def _o(data, choices=None, help=None):
    """Option as extend builtin class (e.g., int, float, str)"""
    class Inner(type(data)):
        def __new__(cls, value, choices=None, help=None):
            self = super().__new__(cls, value)
            self.choices = choices
            self.help = None if help is None else f'{help}. Default: {value!r}.'
            return self
    return Inner(data, choices, help)

class _ob:
    """Option as bool"""
    def __init__(self, data, help=None):
        self._data = data
        self.help = None if help is None else f'{help}. Current: {data!r}.'
        self.is_bool = True
        
    @property
    def data(self):
        return self._data
    
    def __len__(self):
        return self._data
    
    def __eq__(self, other):
        if isinstance(other, _ob) and self._data == other._data:
            return True
        if isinstance(other, bool) and self._data == other:
            return True
        return False
    
    def __repr__(self):
        return repr(self._data)
    
    def __str__(self):
        return str(self._data)

class __ol:
    def __init__(self, data, dtype=None, nargs='*', choices=None, help=None):
        self._data = data
        self.dtype = dtype or (type(data[0]) if data else None)
        self.nargs = nargs
        self.choices = choices
        self.help = None if help is None else f'{help}. Default: {data!r}.'
    
    def __iter__(self):
        return iter(self._data)
    
    def __getitem__(self, index):
        return self._data[index]
    
    def __repr__(self):
        return repr(self._data)
    
    def __str__(self):
        return str(self._data)
    
def _ol(*data, **kwargs):
    """Option as list / tuple"""
    return __ol(data, **kwargs)

# For monitor and all workers
worker_every_path = _ob(False, "If true, use one worker thread (along with one inotify instance) for each of `paths`")
worker_extra_mask = _o('',
    help="Additional inotify events to be recorded into database; if not set, only `route_events` are recorded")
worker_blocking_read = False  # blocking inotify IO / non-blocking inotify IO; both are OK"

# For file tracking
tracker_patterns = _ol(r'.*\.(ini|INI)', r'.*\.(json|JSON)', r'.*\.(txt|TXT)', help="The regex patterns of M types of files")
tracker_filetypes = _ol('INI', 'JSON', 'GENERIC', choices=['INI', 'JSON', 'GENERIC'], help="The corresponding parsers for M types of files")
tracker_indexer = _o('sql', choices=['csv', 'sql'],
    help="How to store file versions; 'csv' for store in an index.csv file, 'sql' for store in database")
tracker_cachetype = _o('sql', choices=['file', 'sql'],
    help="How to store file backups and diffs; 'file' for store as files under `tracker_cachedir`, 'sql' for store in database")
tracker_cachedir = '.track'
tracker_depth = _o(-1, help="The maximum depth of file versions; -1 for infinite depth")
tracker_poolsize = _o(8, help="Size of the connection pool for multi-threaded file processing; Maximum value is 32")

# For message routing
route_tags = _ol('logs', 'warnings', 'tracks', help="The tags of N routes; events satifying all conditions are routed to a tag")
route_patterns = _ol(r'.*', r'.*', r'.*', help="The regex patterns of N routes; only events that fullmatch a pattern are chosen")
route_events = _ol('IN_ALL_EVENTS', 'EX_META', 'EX_MODIFY_CONFIG',
    help="Event filters of N routes; only specified events are chosen; multiple event masks are separated by '|'s")
# TODO: route_types = ('', '', '')  # N watching types ('d' or 'f' for dirs and files, '' for both)
route_formats = _ol('Event {ev_name} on {ev_src}', 'Alert at {msg_time}: {msg}', 'Modified {ev_src}',
    help="The output formats of N routes; fields surrounded by '{' and '}' will be replaced by the formatter")
route_schedulers = _ol('direct', 'direct', 'direct',
    help="The schedulers of N routes; 'direct' / 'proxy' for direct output, 'hist' / 'histogram' for statistical output; "
         "'histogram' can have 3 sub options")
route_default_group = _o('', help="Send messages of all tags to this group by default, if `route_groups` not set")
route_groups = {}  # NOTE: if `tag in route_groups`, send `tag` to that list of groups, otherwise send to default

# For controller
controller_basic_interval = _o(600, help="The interval (seconds) to check worker status")
controller_max_interval = _o(3600*24, help="The maximum interval (seconds) to check worker status")
controller_limit_threshold = _o(0.9, help="Send alert if used inotify instances or watches exceed the ratio")

# For delay queue
buffer_queue_delay = _o(0.5, help="The time (seconds) to leave IN_MOVED_FROM, IN_MODIFY in delay queue for event matching")

# For database
db_enabled = _ob(True, "Enable / disable database")
db_host = 'localhost'
db_user = 'root'
db_password = 'password'
db_database = 'fswatch_db'

# For debug only
external_libs = _ol(dtype=str, help="External python lib paths to be appended to sys.path")
dispatcher_type = _o('redis', choices=['redis', 'local'], help="Choose 'local' to debug locally")