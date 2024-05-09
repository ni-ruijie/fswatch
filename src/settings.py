# For file tracking
cache_dir = '.track'
tracked_pattern = r'.*\.(ini|INI)'

# For message routing
tags = ('logs', 'warnings')  # N destinations
patterns = (r'.*', r'.*')  # N watching re patterns
events = ('IN_ALL_EVENTS', 'META_META')  # N watching events

# For controller
basic_controller_interval = 3600
controller_limit_threshold = 0.9

# For delay queue
buffer_queue_delay = 0.5

# For debug only
dispatcher_type = 'local'