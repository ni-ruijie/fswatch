# For worker
worker_every_path = False  # if true, use one worker thread (along with an inotify instance) for each path

# For file tracking
tracker_cachedir = '.track'
tracker_patterns = (r'.*\.(ini|INI)', r'.*\.py')  # M tracking re patterns
tracker_filetypes = ('INI', 'GENERIC')  # M corresponding parser types
tracker_indexer = 'sql'  # options: csv, sql

# For message routing
route_tags = ('logs', 'warnings', 'tracks')  # N destinations
route_patterns = (r'.*', r'.*', r'.*')  # N watching re patterns
route_events = ('IN_ALL_EVENTS|EX_RENAME', 'EX_META', 'EX_MODIFY_CONFIG')  # N watching events
# TODO: route_types = ('', '')  # N watching types
route_formats = ('Event {ev_name} on {ev_src}', '{msg}', 'Modified {ev_src}')  # N output formats

# For controller
basic_controller_interval = 3600
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
dispatcher_type = 'local'