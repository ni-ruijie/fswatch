# Provide basic logging functions.
# A simple replacement of the loguru package (default format).

from datetime import datetime
import colorama


class Logger:
    def __init__(self):
        self._options = None

    def trace(__self, __message, *args, **kwargs):  # noqa: N805
        r"""Log ``message.format(*args, **kwargs)`` with severity ``'TRACE'``."""
        __self._log("TRACE", False, __self._options, __message, args, kwargs)

    def debug(__self, __message, *args, **kwargs):  # noqa: N805
        r"""Log ``message.format(*args, **kwargs)`` with severity ``'DEBUG'``."""
        __self._log("DEBUG", False, __self._options, __message, args, kwargs)

    def info(__self, __message, *args, **kwargs):  # noqa: N805
        r"""Log ``message.format(*args, **kwargs)`` with severity ``'INFO'``."""
        __self._log("INFO", False, __self._options, __message, args, kwargs)

    def success(__self, __message, *args, **kwargs):  # noqa: N805
        r"""Log ``message.format(*args, **kwargs)`` with severity ``'SUCCESS'``."""
        __self._log("SUCCESS", False, __self._options, __message, args, kwargs)

    def warning(__self, __message, *args, **kwargs):  # noqa: N805
        r"""Log ``message.format(*args, **kwargs)`` with severity ``'WARNING'``."""
        __self._log("WARNING", False, __self._options, __message, args, kwargs)

    def error(__self, __message, *args, **kwargs):  # noqa: N805
        r"""Log ``message.format(*args, **kwargs)`` with severity ``'ERROR'``."""
        __self._log("ERROR", False, __self._options, __message, args, kwargs)

    def critical(__self, __message, *args, **kwargs):  # noqa: N805
        r"""Log ``message.format(*args, **kwargs)`` with severity ``'CRITICAL'``."""
        __self._log("CRITICAL", False, __self._options, __message, args, kwargs)

    def _log(self, level, from_decorator, options, message, args, kwargs):
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ansi_set = ''
        ansi_reset = ''
        if level == 'DEBUG':
            ansi_set, ansi_reset = colorama.Fore.LIGHTBLUE_EX, colorama.Fore.RESET
        elif level == 'INFO':
            ansi_set, ansi_reset = colorama.Style.BRIGHT, colorama.Style.NORMAL
        elif level == 'SUCCESS':
            ansi_set, ansi_reset = colorama.Fore.LIGHTGREEN_EX, colorama.Fore.RESET
        elif level == 'WARNING':
            ansi_set, ansi_reset = colorama.Fore.LIGHTYELLOW_EX, colorama.Fore.RESET
        elif level == 'ERROR':
            ansi_set, ansi_reset = colorama.Fore.LIGHTRED_EX, colorama.Fore.RESET
        elif level == 'CRITICAL':
            ansi_set, ansi_reset = colorama.Back.RED, colorama.Back.RESET
            
        print(f'{colorama.Fore.GREEN}{now}{colorama.Fore.RESET} | {ansi_set}{level:8s}{ansi_reset} | {ansi_set}{message}{ansi_reset}')


logger = Logger()