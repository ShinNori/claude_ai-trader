"""Read an offline period history bundle without persistence."""
from __future__ import annotations

import json
import sys

from .period_evidence_history_fixture import inspect_period_evidence_history
from .packet_cli import _mapping
from .strict_input_cli import _Parser


_ERROR = '期間証拠履歴を検査できません。入力形式と系列の参照を確認してください。'


def main(argv=None):
    try:
        parser = _Parser(description='人工期間証拠の履歴をメモリ内で検査します。')
        parser.add_argument('--input', required=True)
        args = parser.parse_args(argv)
        result = inspect_period_evidence_history(_mapping(args.input))
        rendered = json.dumps(
            result, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except Exception:
        sys.stderr.write(_ERROR + '\n')
        return 2
    sys.stdout.write(rendered + '\n')
    return 0 if result.get('status') == 'VERIFIED_OFFLINE_PERIOD_HISTORY' else 2


if __name__ == '__main__':
    raise SystemExit(main())
