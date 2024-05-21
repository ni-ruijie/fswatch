import os
import os.path as osp
import json
import string
from tabulate import tabulate
from loguru import logger
import settings


def load_json(path: str) -> dict:
    with open(path, 'r') as fi:
        return json.load(fi)


def save_json(obj: dict, path: str) -> None:
    with open(path, 'w') as fo:
        json.dump(obj, fo)


class Formatter(string.Formatter):
    def format_field(self, value, format_spec: str):
        if format_spec.endswith('!none'):
            if value is None:
                return ''
            format_spec = format_spec.rstrip('!none')
        if isinstance(value, dict) and format_spec == 'tab':
            try:  # TODO: deal with 2d table Dict[str, List]
                return tabulate([map(str, value.values())], headers=map(str, value.keys()))
            except:
                pass
        elif format_spec == 'read':
            from tracker import BaseFile, FileDiff
            if isinstance(value, BaseFile):
                return value.to_raw()
            elif isinstance(value, FileDiff):
                return value.to_tree()
        return super().format_field(value, format_spec)


_fmt = Formatter()


def format(s, **kwargs):
    return _fmt.format(s, **kwargs)


def overwrite_settings(parser=None, argv=None):
    import argparse
    from collections.abc import Iterable
    import json

    parser = parser or argparse.ArgumentParser()
    parser.add_argument('--config_files', type=str, nargs='*', default=[])

    # Add settings.* to exec options
    items = {}
    for item in dir(settings):
        if not item.startswith('_'):
            value = getattr(settings, item)
            kwargs = {k: getattr(value, k) for k in ('choices', 'help') if hasattr(value, k)}
            if isinstance(value, dict):
                continue  # can only by modified by json config files
            elif isinstance(value, Iterable) and not isinstance(value, str):
                parser.add_argument(
                    f'--{item}',
                    type=value.dtype if hasattr(value, 'dtype') else type(value[0]),
                    nargs='*', default=None, **kwargs)
            elif isinstance(value, bool) or hasattr(value, 'is_bool'):
                if value:
                    parser.add_argument(f'--{item}', action='store_false', **kwargs)
                else:
                    parser.add_argument(f'--{item}', action='store_true', **kwargs)
            else:
                parser.add_argument(f'--{item}', type=type(value), default=None, **kwargs)
            items[item] = value

    args = parser.parse_args(argv.split(' ') if type(argv) == str else argv)

    for config_file in args.config_files:
        logger.info(f'{config_file} > settings')
        with open(config_file, 'r') as fi:
            cfg = json.load(fi)
        for item in items:
            if item in cfg:
                value = type(items[item])(cfg[item])
                setattr(settings, item, value)
                logger.info(f'settings.{item} = {value!r}')

    logger.info(f'arguments > settings')
    for item in items:
        value = getattr(args, item)
        if value is not None and getattr(settings, item) != value:
            setattr(settings, item, value)
            logger.info(f'settings.{item} = {value!r}')

    return args
