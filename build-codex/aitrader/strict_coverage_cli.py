"""Inspect declared offline page/record coverage without importing market data."""
from __future__ import annotations

import json
import sys

from .packet_cli import _mapping
from .strict_input_cli import _Parser
from .strict_coverage import inspect_strict_coverage

_ERROR = '対象範囲の検査に失敗しました。入力形式と原本の対応を確認してください。'


def main(argv=None):
    try:
        parser = _Parser(description='人工原本のページと銘柄日付の不足を検査します。')
        parser.add_argument('--input', required=True)
        args = parser.parse_args(argv)
        result = inspect_strict_coverage(_mapping(args.input))
        rendered = json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except Exception:
        sys.stderr.write(_ERROR + '\n')
        return 2
    sys.stdout.write(rendered + '\n')
    return 0 if result.get('status') == 'VERIFIED_OFFLINE_COVERAGE' else 2


if __name__ == '__main__':
    raise SystemExit(main())
