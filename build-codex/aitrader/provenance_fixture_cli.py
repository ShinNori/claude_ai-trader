"""Command-line entry point for the isolated offline provenance fixture."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime

from .packet_cli import _mapping
from .provenance_fixture import ingest_legacy_fixture_with_provenance
from .provenance_fixture_inspection import inspect_provenance_fixture


_ERROR = '仮データ専用の市場来歴操作に失敗しました。入力と読取診断を確認してください。'


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(_ERROR)


def _date(value):
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(_ERROR) from None
    if parsed.isoformat() != value:
        raise ValueError(_ERROR)
    return parsed


def _aware(value):
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(_ERROR) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(_ERROR)
    return parsed


def _parser():
    parser = _Parser(
        prog='provenance-fixture',
        description='外部通信を行わない仮データ専用の市場来歴検証')
    commands = parser.add_subparsers(dest='command', required=True,
                                     parser_class=_Parser)
    ingest = commands.add_parser(
        'ingest',
        help='固定した仮データfixtureを専用の新規homeへ記録')
    ingest.add_argument('--home', required=True)
    ingest.add_argument('--fixture', required=True)
    ingest.add_argument('--from', dest='start', required=True)
    ingest.add_argument('--to', dest='end', required=True)
    ingest.add_argument('--recorded-at', required=True)
    inspect = commands.add_parser(
        'inspect',
        help='仮データfixtureの保存来歴だけを読取検査')
    inspect.add_argument('--home', required=True)
    return parser


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
        if args.command == 'ingest':
            # Complete all input parsing and bounded fixture reading before the
            # ingestion API can create or alter the requested home.
            start = _date(args.start)
            end = _date(args.end)
            recorded_at = _aware(args.recorded_at)
            fixture = _mapping(args.fixture)
            result = ingest_legacy_fixture_with_provenance(
                args.home, start=start, end=end, recorded_at=recorded_at,
                fixture=fixture)
            output = {**result, 'fixture_only': True, 'current_signal': False,
                      'ready_for_live': False}
            sys.stdout.write(json.dumps(output, ensure_ascii=False,
                                        sort_keys=True, allow_nan=False) + '\n')
            return 0
        output = inspect_provenance_fixture(args.home)
        sys.stdout.write(json.dumps(output, ensure_ascii=False,
                                    sort_keys=True, allow_nan=False) + '\n')
        return 0 if output.get('status') == 'VERIFIED_FIXTURE' else 2
    except Exception:
        sys.stderr.write(_ERROR + '\n')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
