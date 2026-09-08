import hashlib
from contextlib import closing
import json
import sqlite3
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'ops'), str(ROOT/'build-codex'), str(ROOT/'common/tests/phase2')]
from test_ops_rereview import make_ledger, proposal, trade, csv, AT
from aitrader_ops.ledger import Ledger, LedgerError, MigrationError
from aitrader_ops.models import PositionIn
from aitrader_ops.migrate import check, apply


@pytest.fixture
def legacy(tmp_path):
    path = tmp_path/'old-copy.sqlite'
    l = Ledger(path)
    l.init_snapshot(1000000, [PositionIn('6857',100,1000.)], [], AT)
    l.create_notice(proposal(), at=AT); l.close()
    with closing(sqlite3.connect(path)) as con, con:
        con.execute('INSERT INTO ledger_events(event_id,kind,at,payload,recorded_at) VALUES(?,?,?,?,?)',
                    ['legacy-split','ADJUST',AT.isoformat(),json.dumps(dict(kind='SPLIT',code='6857',ratio=2)),AT.isoformat()])
    return path


def test_m01_error_has_location(legacy):
    with pytest.raises(MigrationError) as error:
        Ledger(legacy)
    assert error.value.seq == 3 and error.value.kind == 'ADJUST' and error.value.reason


def test_m02_check_is_readonly(legacy):
    before = hashlib.sha256(legacy.read_bytes()).hexdigest()
    result = check(legacy)
    assert result['violation']['seq'] == 3 and result['eligible']
    assert hashlib.sha256(legacy.read_bytes()).hexdigest() == before


def test_m03_apply_copy_retains_history_and_new_rules(legacy):
    before = legacy.read_bytes()
    result = apply(legacy)
    assert legacy.read_bytes() == before
    with closing(sqlite3.connect(legacy)) as old, old, closing(sqlite3.connect(result['output'])) as new, new:
        assert old.execute('SELECT * FROM ledger_events').fetchall() == new.execute('SELECT * FROM ledger_events WHERE seq<=3').fetchall()
    l = Ledger(result['output'])
    try:
        assert l.replay_known(3).positions['6857'].qty == 200
        assert l.positions()['6857'].qty == 200
        with pytest.raises(LedgerError):
            l.adjust('SPLIT', None, '6857', 2, AT, 'post marker')
    finally:
        l.close()


def test_m04_existing_output_not_overwritten(legacy):
    apply(legacy)
    with pytest.raises(FileExistsError):
        apply(legacy)


def test_m05_csv_stores_binding_and_keeps_original_audit(make_ledger):
    l = make_ledger(); l.create_notice(proposal(), at=AT)
    assert l.import_csv_fills([csv()]).applied
    assert l.import_csv_fills([csv()]).skipped
    with closing(sqlite3.connect(l.path)) as con, con:
        p = json.loads(con.execute("SELECT payload FROM ledger_events WHERE kind='CSV_FILL'").fetchone()[0])
        assert p['_applied_proposal_id'] == 'buy1'
        assert con.execute('SELECT reason_code FROM ingest_attempts ORDER BY rowid DESC LIMIT 1').fetchone()[0] == 'DUPLICATE'


def test_m06_cashout_is_separate_cash_event(make_ledger):
    l = make_ledger(positions=[PositionIn('6857',101,1000.)])
    l.adjust('FRACTIONAL_CASHOUT', 500, '6857', None, AT, 'confirmed settlement')
    assert l.cash() == 1000500 and l.positions()['6857'].qty == 101


def test_m07_audit_error_chains_original_validation(make_ledger):
    l = make_ledger(); l.create_notice(proposal(), at=AT)
    with closing(sqlite3.connect(l.path)) as con, con:
        con.execute("CREATE TRIGGER deny BEFORE INSERT ON ingest_attempts BEGIN SELECT RAISE(FAIL,'audit failed'); END")
    with pytest.raises(sqlite3.DatabaseError) as error:
        l.report(trade(qty=200))
    assert isinstance(error.value.__cause__, LedgerError)
    assert l.cash() == 1000000


def test_m08_untimed_csv_cannot_change_cash(make_ledger):
    l = make_ledger(); l.create_notice(proposal(), at=AT)
    assert l.import_csv_fills([csv(at=None)]).pending
    assert l.cash() == 1000000
