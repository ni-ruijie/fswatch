# For file tracking
cache_dir = '.track'
tracked_patterns = (r'.*\.(ini|INI)', r'.*\.py')  # M tracking re patterns
tracked_filetypes = ('INI', 'GENERIC')  # M corresponding parser types

# For message routing
route_tags = ('logs', 'warnings')  # N destinations
route_patterns = (r'.*', r'.*')  # N watching re patterns
route_events = ('IN_ALL_EVENTS|EX_RENAME', 'EX_META')  # N watching events
# TODO: route_types = ('', '')  # N watching types
# TODO: route_formats = ('{event} {path}', '{msg}')  # N output formats

# For controller
basic_controller_interval = 3600
controller_limit_threshold = 0.9

# For delay queue
buffer_queue_delay = 0.5

# For debug only
dispatcher_type = 'local'