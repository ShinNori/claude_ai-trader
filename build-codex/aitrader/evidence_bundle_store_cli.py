"""CLI for bounded offline evidence bundle storage and reproduction."""
from __future__ import annotations

import json
import sys

from .evidence_bundle_store import put, verify
from .strict_input_cli import _Parser


_ERROR = '人工束を保存・再読できません。保存先と入力形式を確認してください。'


def main(argv=None):
    try:
        parser = _Parser(description='人工結合 v2 束をオフライン保存・再読します。')
        commands = parser.add_subparsers(dest="command", required=True)
        put_parser = commands.add_parser("put")
        put_parser.add_argument("--input", required=True)
        put_parser.add_argument("--home")
        verify_parser = commands.add_parser("verify")
        verify_parser.add_argument("--id", required=True)
        verify_parser.add_argument("--home")
        args = parser.parse_args(argv)
        if args.command == "put":
            result = put(args.input, home=args.home)
        else:
            result = verify(args.id, home=args.home)
        rendered = json.dumps(
            result, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except Exception:
        sys.stderr.write(_ERROR + "\n")
        return 2
    sys.stdout.write(rendered + "\n")
    return 0 if result.get("status") in {"STORED", "NO_OP", "REPRODUCED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
