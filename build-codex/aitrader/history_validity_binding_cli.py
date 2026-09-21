"""Read an offline history/validity binding bundle without persistence."""
from __future__ import annotations

import json
import sys

from .history_validity_binding import inspect_history_validity_binding
from .packet_cli import _mapping
from .strict_input_cli import _Parser


_ERROR = '履歴と適用期間を検査できません。入力形式とサイズを確認してください。'


def main(argv=None):
    try:
        parser = _Parser(description='人工履歴と適用期間の結合をメモリ内で検査します。')
        parser.add_argument('--input', required=True)
        args = parser.parse_args(argv)
        result = inspect_history_validity_binding(_mapping(args.input))
        rendered = json.dumps(
            result, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except Exception:
        sys.stderr.write(_ERROR + '\n')
        return 2
    sys.stdout.write(rendered + '\n')
    return 0 if result.get('status') == 'VERIFIED_OFFLINE_BINDING' else 2


if __name__ == '__main__':
    raise SystemExit(main())
