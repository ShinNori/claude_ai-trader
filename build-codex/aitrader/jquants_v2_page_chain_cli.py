"""Inspect a recorded artificial pagination chain without contacting J-Quants."""
from __future__ import annotations

import json
import sys

from .packet_cli import _mapping
from .strict_input_cli import _Parser
from .jquants_v2_page_chain import inspect_v2_page_chain

_ERROR = 'ページ記録を検査できません。入力形式と取得順の記録を確認してください。'


def main(argv=None):
    try:
        parser = _Parser(description='人工V2応答のページ連鎖を読取検査します。')
        parser.add_argument('--input', required=True)
        args = parser.parse_args(argv)
        result = inspect_v2_page_chain(_mapping(args.input))
        rendered = json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except Exception:
        sys.stderr.write(_ERROR + '\n')
        return 2
    sys.stdout.write(rendered + '\n')
    return 0 if result.get('status') == 'VERIFIED_OFFLINE_PAGE_CHAIN' else 2


if __name__ == '__main__':
    raise SystemExit(main())
