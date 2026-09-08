"""Read-only inspection and append-only policy upgrade on a NEW backup copy."""
import argparse
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .ledger import Ledger, MigrationError


def _source(path):
    path = Path(path).resolve(strict=True)
    return sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)


def _replayer():
    # No DB opened or schema modified while checking an input file.
    reader = object.__new__(Ledger)
    reader._init_fee_margin = Decimal('.002')
    return reader


def check(path):
    with closing(_source(path)) as con:
        rows = con.execute('SELECT seq,kind,payload FROM ledger_events ORDER BY seq').fetchall()
    if not rows:
        raise ValueError('初期履歴がありません')
    reader = _replayer()
    result = dict(source=str(Path(path).resolve()), last_seq=rows[-1][0], violation=None)
    try:
        current = reader._rebuild(rows)
        result['current_balance'] = asdict(reader._view_of(current))
    except MigrationError as exc:
        result['violation'] = dict(seq=exc.seq, kind=exc.kind, reason=exc.reason)
    old = reader._rebuild(rows, legacy=True)
    result['legacy_balance'] = asdict(reader._view_of(old))
    result['already_upgraded'] = any(k == 'POLICY_UPGRADE' for _, k, _ in rows)
    # No implicit revaluation: differing balances require an explicit migration decision.
    result['eligible'] = (result['violation'] is not None or
                          result['current_balance'] == result['legacy_balance'])
    return result


def apply(path):
    source = Path(path).resolve(strict=True)
    target = source.with_name(source.stem+'.upgraded.sqlite')
    if target.exists():
        raise FileExistsError(target)
    fd, name = tempfile.mkstemp(prefix=source.stem+'.', suffix='.upgrading.sqlite', dir=source.parent)
    os.close(fd)
    working = Path(name).resolve()
    if working.parent != source.parent or working == source or working == target:
        raise ValueError('作業コピーの保存先が不正です')
    try:
        src = _source(source)
        dst = sqlite3.connect(working)
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()
        inspection = check(working)
        if inspection['already_upgraded']:
            raise ValueError('移行済みです。追加マーカーは作成しません')
        if not inspection['eligible']:
            raise ValueError('旧規則と新規則の残高が異なります。自動移行できません')
        at = datetime.now(timezone.utc).isoformat()
        marker = dict(schema_version=3, at=at, legacy_last_seq=inspection['last_seq'])
        with closing(sqlite3.connect(working)) as con:
            con.execute('INSERT INTO ledger_events(event_id,kind,at,payload,recorded_at) VALUES(?,?,?,?,?)',
                        [f"policy-upgrade:{inspection['last_seq']}", 'POLICY_UPGRADE', at, json.dumps(marker), at])
            con.commit()
        l = Ledger(working)
        try:
            past = asdict(l.replay_known(inspection['last_seq']))
            if past != inspection['legacy_balance'] or asdict(l.view()) != past:
                raise ValueError('移行前残高と旧最終履歴の再計算が一致しません')
        finally:
            l.close()
        if os.name == 'nt':
            working.rename(target)  # atomic, refuses existing destinations on Windows
        else:
            os.link(working, target)  # atomic no-replace publication on POSIX
            working.unlink()
        return dict(output=str(target), old_last_seq=inspection['last_seq'], verified=True,
                    balance=past, warning=inspection['violation'])
    finally:
        # Only this invocation's unique temporary file and SQLite sidecars.
        for artifact in [working, Path(str(working)+'-wal'), Path(str(working)+'-shm'), Path(str(working)+'-journal')]:
            if artifact.parent.resolve() != source.parent or artifact == source or artifact == target:
                raise ValueError('作業コピーの削除先が不正です')
            artifact.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check', metavar='COPY')
    group.add_argument('--apply', metavar='COPY')
    args = parser.parse_args()
    try:
        result = check(args.check) if args.check else apply(args.apply)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except MigrationError as exc:
        print(json.dumps(dict(error='MigrationError', seq=exc.seq, kind=exc.kind, reason=exc.reason), ensure_ascii=False))
    except Exception as exc:
        print(json.dumps(dict(error=type(exc).__name__, reason=str(exc)), ensure_ascii=False))
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
