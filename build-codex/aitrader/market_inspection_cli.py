"""Read-only market input inspection; stdout only, never a trading signal."""
import argparse
from datetime import date
import json
from pathlib import Path

from .market_inspection import inspect_market_inputs


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, '市場入力検査の引数を確認してください。--help を参照してください。\n')


def main(argv=None):
    parser = _Parser(description='既存市場DBの読取検査。売買承認や自動修復は行いません。')
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--as-of', required=True, help='検査の基準日 YYYY-MM-DD')
    args = parser.parse_args(argv)
    try:
        as_of = date.fromisoformat(args.as_of)
    except ValueError:
        parser.error('invalid date')
    result = inspect_market_inputs(args.home, as_of=as_of)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0 if result['status'] == 'INSPECTED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
