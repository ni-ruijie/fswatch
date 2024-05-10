# For file tracking
cache_dir = '.track'
tracked_patterns = (r'.*\.(ini|INI)', r'.*\.py')  # M tracking re patterns
tracked_filetypes = ('INI', 'GENERIC')  # M corresponding parser types

# For message routing
route_tags = ('logs', 'warnings', 'tracks')  # N destinations
route_patterns = (r'.*', r'.*', r'.*')  # N watching re patterns
route_events = ('IN_ALL_EVENTS|EX_RENAME', 'EX_META', 'EX_MODIFY_CONFIG')  # N watching events
# TODO: route_types = ('', '')  # N watching types
# TODO: route_formats = ('{event} {path}', '{msg}')  # N output formats

# For controller
basic_controller_interval = 3600
controller_limit_threshold = 0.9

# For delay queue
buffer_queue_delay = 0.5

# For database
db_host = 'localhost'
db_user = 'root'
db_password = 'password'

# For debug only
dispatcher_type = 'local'