"""Read-only CLI for inspecting persisted notification STOP sources."""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from aitrader_ops.stop_status import inspect_stop_status
from .runner import encoded


def main(argv=None):
    parser = argparse.ArgumentParser(description='停止状態の読取専用診断（解除・修復なし）')
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--now', required=True, help='タイムゾーン付きISO日時')
    parser.add_argument('--stop-file', type=Path, action='append', default=[])
    args = parser.parse_args(argv)
    try:
        observed_at = datetime.fromisoformat(args.now)
        result = inspect_stop_status(args.state, now=observed_at, stop_files=args.stop_file)
    except ValueError:
        parser.error('日時にはタイムゾーン付きISO日時を指定してください')
    print(encoded(result))
    return 0 if result['known'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
