"""Read an offline evidence bundle without ingesting or creating trading state."""
from __future__ import annotations

import argparse
import json
import sys

from .packet_cli import _mapping
from .strict_input import inspect_strict_input

_ERROR = '専用入力の検査に失敗しました。入力形式と原本の対応を確認してください。'


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(_ERROR)


def main(argv=None):
    try:
        parser = _Parser(description='原本付き人工入力を検査する専用モード。売買承認や取込は行いません。')
        parser.add_argument('--input', required=True)
        args = parser.parse_args(argv)
        result = inspect_strict_input(_mapping(args.input))
        rendered = json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except Exception:
        sys.stderr.write(_ERROR + '\n')
        return 2
    sys.stdout.write(rendered + '\n')
    return 0 if result.get('status') == 'VERIFIED_OFFLINE_INPUT' else 2


if __name__ == '__main__':
    raise SystemExit(main())
