"""Inspect explicit validity periods of artificial lot/event evidence."""
from __future__ import annotations

import json
import sys

from .packet_cli import _mapping
from .strict_input_cli import _Parser
from .strict_validity import inspect_strict_validity

_ERROR = '適用期間の検査に失敗しました。入力形式と原本の対応を確認してください。'


def main(argv=None):
    try:
        parser = _Parser(description='人工原本の単元・イベント適用期間を検査します。')
        parser.add_argument('--input', required=True)
        args = parser.parse_args(argv)
        result = inspect_strict_validity(_mapping(args.input))
        rendered = json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except Exception:
        sys.stderr.write(_ERROR + '\n')
        return 2
    sys.stdout.write(rendered + '\n')
    return 0 if result.get('status') == 'VERIFIED_OFFLINE_VALIDITY' else 2


if __name__ == '__main__':
    raise SystemExit(main())
