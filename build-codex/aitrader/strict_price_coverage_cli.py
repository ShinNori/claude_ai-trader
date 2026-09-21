"""Check selected price dates against a declared offline coverage request."""
from __future__ import annotations

import json
import sys

from .packet_cli import _mapping
from .strict_input_cli import _Parser
from .strict_price_coverage import inspect_strict_price_coverage

_ERROR = '価格原本の範囲照合に失敗しました。入力と要求範囲を確認してください。'


def main(argv=None):
    try:
        parser = _Parser(description='検査済み人工価格原本から銘柄日付の範囲を照合します。')
        parser.add_argument('--input', required=True)
        parser.add_argument('--request', required=True)
        args = parser.parse_args(argv)
        result = inspect_strict_price_coverage(_mapping(args.input), _mapping(args.request))
        rendered = json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except Exception:
        sys.stderr.write(_ERROR + '\n')
        return 2
    sys.stdout.write(rendered + '\n')
    return 0 if result.get('status') == 'VERIFIED_OFFLINE_PRICE_COVERAGE' else 2


if __name__ == '__main__':
    raise SystemExit(main())
