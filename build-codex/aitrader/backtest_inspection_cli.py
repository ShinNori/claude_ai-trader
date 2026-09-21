"""Inspect saved research outputs without repairing or resuming anything."""
import argparse
import json
from pathlib import Path

from .backtest_inspection import inspect_backtest_results


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, '結果検査の引数を確認してください。--help を参照してください。\n')


def main(argv=None):
    parser = _Parser(description='保存結果の読取検査。ロック解除・復元・売買承認は行いません。')
    parser.add_argument('--results', type=Path, required=True)
    args = parser.parse_args(argv)
    result = inspect_backtest_results(args.results)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0 if result['status'] == 'OBSERVED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
