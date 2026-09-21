"""CLI for offline shape inspection of a proposed J-Quants V2 JSON page."""
from __future__ import annotations

import argparse
import json
import sys

from .jquants_v2_contract import inspect_v2_page
from .packet_cli import _mapping


_ERROR = 'V2仮データのローカル形状検査に失敗しました'
_DATASETS = ('prices', 'margin', 'master', 'topix', 'calendar')


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(_ERROR) from None


def _parser():
    parser = _Parser(
        prog='jquants-v2-inspect',
        description='通信を行わずV2仮データJSONの形状だけを検査します')
    parser.add_argument('--dataset', choices=_DATASETS, required=True)
    parser.add_argument('--input', required=True)
    return parser


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
        payload = _mapping(args.input)
        summary = inspect_v2_page(args.dataset, payload)
        output = {**summary, 'fixture_only': True, 'current_signal': False}
        sys.stdout.write(json.dumps(output, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False) + '\n')
        return 0
    except Exception:
        sys.stderr.write(_ERROR + '\n')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

