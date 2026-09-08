"""Independent v0.3.6 checks; only synthetic ledgers and the local migration CLI."""
from contextlib import closing
from datetime import timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'ops'), str(ROOT / 'ops/tests'),
                str(ROOT / 'common/tests/phase2')]
from test_ops_rereview import make_ledger, proposal, trade, csv, AT
from test_migration import legacy
from test_v034_contracts import provenance
from aitrader_ops.ledger import Ledger, LedgerError, MigrationError
from aitrader_ops.migrate import apply
from aitrader_ops.models import PositionIn


def assert_empty(view):
    assert view.cash == view.available == view.reserved == 0
    assert view.positions == view.reserved_positions == view.reserved_shares == {}


def test_o01_resolved_pending_before_late_notice_replays(make_ledger):
    """保留後着通知へのAPPLYも、約定と同様に未来予約を漏らさず業務時点再生する。"""
    ledger = make_ledger()
    pending_at = AT + timedelta(hours=1)
    resolved_at = AT + timedelta(hours=2)
    notice_at = AT + timedelta(hours=3)
    assert ledger.import_csv_fills([csv('unknown', at=pending_at)]).pending == ['csv1']
    ledger.create_notice(proposal('target'), at=notice_at)
    ledger.resolve_pending('csv1', 'target', resolved_at, 'APPLY')
    assert ledger.pending_rows() == {}
    assert ledger.cash() == 900000 and ledger.positions()['6857'].qty == 100
    assert ledger.replay(pending_at).cash == 1000000
    # The accepted operation must have a valid business-time view too.
    at_resolution = ledger.replay(resolved_at)
    assert at_resolution.cash == 900000 and at_resolution.positions['6857'].qty == 100
    assert at_resolution.reserved == 0
    assert ledger.replay(notice_at) == ledger.view()


@pytest.mark.parametrize('with_provenance', [False, True])
def test_o02_actual_correction_after_snapshot_and_empty_before(make_ledger, with_provenance):
    """実際に適用された約定・訂正を含めてもSNAPSHOT前は空、訂正前後の金額は別々に再生。"""
    ledger = make_ledger(initialize=False)
    ledger.init_snapshot(1000000, [], [], AT,
                         provenance=provenance() if with_provenance else None)
    ledger.create_notice(proposal(), at=AT)
    assert ledger.report(trade(at=AT + timedelta(hours=1))).applied
    assert ledger.report(trade(eid='correction', kind='CORRECTION', price=900.,
                               replaces_event_id='fill1', at=AT + timedelta(hours=2))).applied
    assert_empty(ledger.replay((AT - timedelta(microseconds=1)).astimezone(timezone.utc)))
    assert ledger.replay(AT + timedelta(hours=1)).cash == 900000
    assert ledger.replay(AT + timedelta(hours=2)).cash == 910000
    with closing(Ledger(ledger.path)) as reopened:
        assert_empty(reopened.replay(AT - timedelta(microseconds=1)))
        assert reopened.replay() == reopened.view() == ledger.view()


def test_o03_timeless_pending_apply_has_resolution_boundary(make_ledger):
    """時刻なし保留へのAPPLYは解決日時で残高に入り、それ以前を先取りしない。"""
    ledger = make_ledger()
    ledger.create_notice(proposal(), at=AT)
    assert ledger.import_csv_fills([csv('buy1', at=None)]).pending == ['csv1']
    at = AT + timedelta(hours=1)
    ledger.resolve_pending('csv1', 'buy1', at.astimezone(timezone.utc), 'APPLY')
    assert ledger.replay(at - timedelta(microseconds=1)).cash == 1000000
    assert ledger.replay(at).cash == 900000
    with closing(Ledger(ledger.path)) as reopened:
        assert reopened.pending_rows() == {}
        assert reopened.replay(at) == reopened.view()


@pytest.mark.parametrize('after_marker', [False, True])
def test_o04_inverse_history_cli_reports_position_and_preserves_source(tmp_path, after_marker):
    """旧履歴または移行後の逆順解決を、実migrate --checkが位置付きで報告し原本を保存する。"""
    source = tmp_path / '日本語 inverse.sqlite'
    with closing(Ledger(source)) as ledger:
        ledger.init_snapshot(1000000, [], [], AT)
    if after_marker:
        source = Path(apply(source)['output'])
    with closing(Ledger(source)) as ledger:
        assert ledger.import_csv_fills([csv('unknown', at=AT + timedelta(hours=2))]).pending
        last_seq = ledger.seq()
    with closing(sqlite3.connect(source)) as con, con:
        con.execute('INSERT INTO ledger_events(event_id,kind,at,payload,recorded_at) VALUES(?,?,?,?,?)',
                    ['legacy-inverse', 'PENDING_RESOLVED', AT.isoformat(),
                     json.dumps(dict(source_event_id='csv1', action='DISCARD', at=AT.isoformat())),
                     AT.isoformat()])
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(MigrationError) as caught:
        Ledger(source)
    assert (caught.value.seq, caught.value.kind) == (last_seq + 1, 'PENDING_RESOLVED')
    result = subprocess.run([sys.executable, '-m', 'aitrader_ops.migrate', '--check', str(source)],
                            cwd=ROOT / 'ops', capture_output=True, text=True,
                            encoding='utf-8', env=dict(os.environ, PYTHONUTF8='1'), timeout=20)
    payload = json.loads(result.stdout)
    location = payload.get('violation') or payload
    assert (location['seq'], location['kind']) == (last_seq + 1, 'PENDING_RESOLVED')
    assert result.returncode == (1 if after_marker else 0)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_o05_migrated_pending_boundary_before_and_after_marker(legacy):
    """正規移行の後着保留・同時刻DISCARDで、旧seq境界と現在残高を維持する。"""
    result = apply(legacy)
    with closing(Ledger(result['output'])) as ledger:
        before = ledger.view()
        assert ledger.import_csv_fills([csv('unknown', at=AT + timedelta(hours=2))]).pending
        ledger.resolve_pending('csv1', None, AT + timedelta(hours=2), 'DISCARD')
        assert ledger.replay_known(result['old_last_seq']) == before == ledger.view()
        assert ledger.replay(AT + timedelta(hours=1)) == before
        assert ledger.replay(AT + timedelta(hours=2)) == before
        assert_empty(ledger.replay(AT - timedelta(microseconds=1)))
