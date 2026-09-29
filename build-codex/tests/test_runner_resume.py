"""Contract §8 counterexamples for explicit managed-v1 C2/C3 recovery."""
from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'ops'), str(ROOT/'build-codex'),
                str(ROOT/'common/tests/phase2')]

from aitrader import runner, runner_resume
from aitrader.db import connect, init
from aitrader.managed_stop import apply_managed_control, initialize_managed_mock
from aitrader.packet import Proposal, packet_hash
from aitrader.runner import RunError, Valuation, mock_verdicts, run
from aitrader_ops.ledger import Ledger
from aitrader_ops.limits import Limits
from aitrader_ops.models import JST, PositionIn, TradeEvent, Verdict

DAY = date(2026, 9, 8)
AS_OF = date(2026, 9, 7)
NOW = datetime(2026, 9, 8, 7, 5, tzinfo=JST)
SETTINGS = {'line': {'allowed_user_id': 'resume-fixture', 'monthly_budget': 10}}


def proposal(pid='resume-buy', *, side='BUY', code='7203', price=100.0):
    value = Proposal(pid, '', code, side, 100, 100, price, 'OPENING_LIMIT',
                     'CASH', 'resume', 'v1', AS_OF, 'snapshot', 'review-v1',
                     datetime(2026, 9, 8, 8, 59, tzinfo=JST), 'resume fixture',
                     {'next_earnings_date': None, 'margin_regulated': False})
    return replace(value, packet_hash=packet_hash(value))


def make_home(tmp_path, *, managed=True, cash=1_000_000):
    home = tmp_path/('managed' if managed else 'legacy')
    positions = [PositionIn('6857', 300, 100.0), PositionIn('7203', 300, 100.0)]
    if managed:
        initialize_managed_mock(home, cash, positions, NOW-timedelta(days=1),
                                settings=SETTINGS)
    else:
        runner.initialize_mock(home, cash, positions, NOW-timedelta(days=1))
    init(home)
    with connect(home) as db:
        for offset in range(-3, 4):
            day = DAY+timedelta(days=offset)
            db.execute('INSERT INTO calendar VALUES(?,?)', [day, day.weekday() < 5])
        db.executemany('INSERT INTO prices_daily(code,date,close) VALUES(?,?,?)',
                       [('7203', AS_OF, 100.0), ('6857', AS_OF, 100.0)])
    return home


def execute(home, p=None, *, run_id='resume-run', now=NOW, votes=None,
            unresolved=False, valuation=None, limits=None):
    p = p or proposal()
    votes = votes if votes is not None else mock_verdicts([p], run_id, NOW)
    return run(home, run_id, DAY, [p], votes, now,
               valuation or Valuation(1_060_000, 1_060_000),
               limits=limits,
               unresolved_unconfirmed=unresolved)


def crash_at(home, monkeypatch, point, p=None):
    p = p or proposal()
    if point == 'C1':
        owner, name = Ledger, 'create_notice'
    elif point == 'C2':
        owner, name = Ledger, 'set_notice_state'
    else:
        owner, name = runner_resume, 'finalize_resume'
    original = getattr(owner, name)
    monkeypatch.setattr(owner, name, lambda *a, **k: (_ for _ in ()).throw(RuntimeError(point)))
    with pytest.raises(RuntimeError, match=point):
        execute(home, p)
    monkeypatch.setattr(owner, name, original)
    return p


def ledger_state(home, pid='resume-buy'):
    with closing(Ledger(home/'ledger.sqlite')) as ledger:
        return ledger.seq(), ledger.notice(pid), ledger.reserved(), ledger.view()


def journal_state(home, pid='resume-buy'):
    with sqlite3.connect(home/'orchestration.sqlite') as db:
        candidate = db.execute('SELECT state,result FROM candidates WHERE pid=?', [pid]).fetchone()
        outbox = db.execute("SELECT count(*) FROM outbox WHERE key LIKE '%:CANDIDATE'").fetchone()[0]
        manifest = json.loads(db.execute("SELECT manifest FROM runs WHERE id='resume-run'").fetchone()[0])
    return candidate, outbox, manifest


def test_01_c2_resume_approves_once_without_recreating(monkeypatch, tmp_path):
    home = make_home(tmp_path)
    p = crash_at(home, monkeypatch, 'C2')
    before = ledger_state(home)
    creates = 0
    original = Ledger.create_notice
    def counted(self, *args, **kwargs):
        nonlocal creates
        creates += 1
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Ledger, 'create_notice', counted)
    result = execute(home, p, now=NOW+timedelta(minutes=1))
    after = ledger_state(home)
    assert result['candidates'][0]['resume']['from_state'] == 'CREATED'
    assert creates == 0 and after[2] == before[2] and journal_state(home)[1] == 1


def test_02_c3_resume_writes_journal_only(monkeypatch, tmp_path):
    home = make_home(tmp_path)
    crash_at(home, monkeypatch, 'C2')
    original = runner_resume.finalize_resume
    monkeypatch.setattr(runner_resume, 'finalize_resume',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('C3')))
    with pytest.raises(RuntimeError, match='C3'):
        execute(home, now=NOW+timedelta(seconds=30))
    monkeypatch.setattr(runner_resume, 'finalize_resume', original)
    before = ledger_state(home)[0]
    result = execute(home, now=NOW+timedelta(minutes=1))
    assert result['candidates'][0]['resume']['from_state'] == 'APPROVED'
    assert ledger_state(home)[0] == before


def test_03_c1_uses_existing_normal_path(monkeypatch, tmp_path):
    home = make_home(tmp_path)
    p = crash_at(home, monkeypatch, 'C1')
    creates = 0
    original = Ledger.create_notice
    def counted(self, *args, **kwargs):
        nonlocal creates
        creates += 1
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Ledger, 'create_notice', counted)
    assert execute(home, p, now=NOW+timedelta(minutes=1))['status'] == 'CANDIDATES'
    assert creates == 1


def test_04_repeat_after_resume_is_immutable(monkeypatch, tmp_path):
    home = make_home(tmp_path)
    crash_at(home, monkeypatch, 'C2')
    first = execute(home, now=NOW+timedelta(minutes=1))
    before = ledger_state(home)[0], journal_state(home)[1]
    assert execute(home, now=NOW+timedelta(hours=2)) == first
    assert (ledger_state(home)[0], journal_state(home)[1]) == before


@pytest.mark.parametrize('mutation,match', [
    ('run', '別run'), ('hash', '内容が変わっています')])
def test_05_06_existing_candidate_binding_stops_before_write(monkeypatch, tmp_path,
                                                             mutation, match):
    home = make_home(tmp_path)
    p = crash_at(home, monkeypatch, 'C2')
    with sqlite3.connect(home/'orchestration.sqlite') as db:
        if mutation == 'run':
            db.execute("UPDATE candidates SET owner='other-run'")
        else:
            db.execute("UPDATE candidates SET hash='bad'")
    before = ledger_state(home)[0]
    with pytest.raises(RunError, match=match):
        execute(home, p, now=NOW+timedelta(minutes=1))
    assert ledger_state(home)[0] == before


@pytest.mark.parametrize('case', ['SENT', 'PARTIAL', 'RESERVE', 'OLD_HISTORY'])
def test_07_08_10_11_ownership_mismatches_stop(monkeypatch, tmp_path, case):
    home = make_home(tmp_path)
    crash_at(home, monkeypatch, 'C2')
    original_notice = Ledger.notice
    def altered(self, pid):
        value = original_notice(self, pid)
        if case == 'SENT': value['notice_state'] = 'SENT'
        if case == 'PARTIAL': value.update(filled_qty=1, fills=[{'qty': 1}], trade_state='PARTIAL')
        if case == 'RESERVE': value['reserve'] += 1
        if case == 'OLD_HISTORY': value['history'][0] = ('CREATED', (NOW-timedelta(days=2)).isoformat())
        return value
    monkeypatch.setattr(Ledger, 'notice', altered)
    with pytest.raises(RunError, match='OWNERSHIP_UNPROVEN'):
        execute(home, now=NOW+timedelta(minutes=1))


def test_09_other_same_run_intent_notice_fails_e6(monkeypatch, tmp_path):
    home = make_home(tmp_path)
    p1, p2 = proposal('a'), proposal('b')
    # Manufacture the contract's interrupted two-candidate state through public ledger writes.
    original = Ledger.set_notice_state
    monkeypatch.setattr(Ledger, 'set_notice_state',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('stop-first')))
    with pytest.raises(RuntimeError):
        run(home, 'resume-run', DAY, [p1, p2],
            mock_verdicts([p1, p2], 'resume-run', NOW), NOW,
            Valuation(1_060_000, 1_060_000))
    monkeypatch.setattr(Ledger, 'set_notice_state', original)
    with sqlite3.connect(home/'orchestration.sqlite') as db:
        item = json.loads(db.execute("SELECT result FROM candidates WHERE pid='a'").fetchone()[0])
        item['proposal_id'] = 'b'
        db.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?,?)',
                   ['b', runner.digest(p2), DAY.isoformat(), 'BUY', 'INTENT',
                    runner.encoded(item), 'resume-run'])
    with closing(Ledger(home/'ledger.sqlite')) as ledger:
        ledger.create_notice(p2, NOW)
    with pytest.raises(RunError, match='OWNERSHIP_UNPROVEN'):
        run(home, 'resume-run', DAY, [p1, p2],
            mock_verdicts([p1, p2], 'resume-run', NOW), NOW+timedelta(minutes=1),
            Valuation(1_060_000, 1_060_000))


@pytest.mark.parametrize('second,code', [(15, 'RESUME_WINDOW_CLOSED'), (14, None)])
def test_12_resume_deadline_half_open(monkeypatch, tmp_path, second, code):
    home = make_home(tmp_path)
    crash_at(home, monkeypatch, 'C2')
    at = datetime(2026, 9, 8, 7, 15, tzinfo=JST)-timedelta(seconds=15-second)
    if code:
        with pytest.raises(RunError, match=code): execute(home, now=at)
    else:
        assert execute(home, now=at)['status'] == 'CANDIDATES'


@pytest.mark.parametrize('unknown,code', [(False, 'STOP_ACTIVE'), (True, 'STOP_STATE_UNKNOWN')])
def test_13_buy_stop_active_or_unknown(monkeypatch, tmp_path, unknown, code):
    home = make_home(tmp_path)
    crash_at(home, monkeypatch, 'C2')
    if unknown:
        monkeypatch.setattr('aitrader.managed_stop.inspect_managed_stop',
                            lambda *a, **k: {'known': False, 'effective_stop': True})
    else:
        apply_managed_control(home, action='STOP', now=NOW+timedelta(seconds=1),
                              settings=SETTINGS, event_id='resume-stop')
    with pytest.raises(RunError, match=code):
        execute(home, now=NOW+timedelta(minutes=1))


def test_14_withdrawal_makes_c2_gate_refuse_without_writes(monkeypatch, tmp_path):
    home = make_home(tmp_path)
    basis = Valuation(1_060_000, 1_060_000)
    p = proposal()
    original = Ledger.set_notice_state
    monkeypatch.setattr(Ledger, 'set_notice_state',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('C2')))
    with pytest.raises(RuntimeError, match='C2'):
        execute(home, p, valuation=basis)
    monkeypatch.setattr(Ledger, 'set_notice_state', original)
    with closing(Ledger(home/'ledger.sqlite')) as ledger:
        ledger.adjust('WITHDRAW', 989_980, None, None,
                      NOW+timedelta(seconds=1), 'fixture')
    before = journal_state(home)[:2]
    with pytest.raises(RunError, match='NEEDS_RECONCILIATION'):
        execute(home, p, now=NOW+timedelta(minutes=1), valuation=basis)
    assert journal_state(home)[:2] == before


def test_15_exclusion_keeps_other_same_code_reservation(tmp_path):
    from aitrader_ops.models import LedgerView
    view = LedgerView(1_000, 300, 700, {}, {'7203': 300})
    p = proposal()
    adjusted = runner_resume.reservation_excluded_view(
        view, {'reserve': 100, 'reserved_shares': 0}, p)
    assert adjusted.reserved_positions['7203'] == 200
    sell_view = LedgerView(1_000, 0, 1_000, {}, {},
                           reserved_shares={'7203': 300})
    sell_adjusted = runner_resume.reservation_excluded_view(
        sell_view, {'reserve': 0, 'reserved_shares': 100},
        proposal(side='SELL'))
    assert sell_adjusted.reserved_shares['7203'] == 200


def test_16_sell_c2_excludes_own_shares_and_ignores_stop(monkeypatch, tmp_path):
    home = make_home(tmp_path)
    p = proposal(side='SELL')
    crash_at(home, monkeypatch, 'C2', p)
    apply_managed_control(home, action='STOP', now=NOW+timedelta(seconds=1),
                          settings=SETTINGS, event_id='sell-stop')
    assert execute(home, p, now=NOW+timedelta(minutes=1))['status'] == 'CANDIDATES'


def test_17_seq_change_before_c2_write_refuses(monkeypatch, tmp_path):
    home = make_home(tmp_path)
    crash_at(home, monkeypatch, 'C2')
    original = runner.evaluate
    def changing(*args, **kwargs):
        with closing(Ledger(home/'ledger.sqlite')) as ledger:
            ledger.adjust('DEPOSIT', 1, None, None, NOW, 'seq fixture')
        return original(*args, **kwargs)
    monkeypatch.setattr(runner, 'evaluate', changing)
    with pytest.raises(RunError, match='SEQ_CHANGED'):
        execute(home, now=NOW+timedelta(minutes=1))


def test_18_daily_slot_excludes_self(monkeypatch, tmp_path):
    home = make_home(tmp_path)
    limits = Limits(max_new_per_day=1)
    original = Ledger.set_notice_state
    monkeypatch.setattr(Ledger, 'set_notice_state',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('C2')))
    with pytest.raises(RuntimeError, match='C2'):
        execute(home, limits=limits)
    monkeypatch.setattr(Ledger, 'set_notice_state', original)
    result = execute(home, now=NOW+timedelta(minutes=1), limits=limits)
    assert 'DAILY_NEW_LIMIT' not in result['candidates'][0]['gate']['reason_codes']


@pytest.mark.parametrize('kind,code', [('missing', 'REVIEW_INCOMPLETE'),
                                       ('invalid', 'REVIEW_INVALID'),
                                       ('future', 'REVIEW_INVALID')])
def test_19_vote_helper_rejects_bad_current_approvals(kind, code):
    p = proposal()
    votes = mock_verdicts([p], 'resume-run', NOW)[p.proposal_id]
    if kind == 'missing': votes.pop()
    elif kind == 'invalid': votes[0] = replace(votes[0], decision='INVALID')
    else: votes[0] = replace(votes[0], received_at=NOW+timedelta(hours=1))
    with pytest.raises(runner_resume.ResumeRefused, match=code):
        runner_resume.validate_verdicts(p, votes, run_id='resume-run',
            execution_day=DAY, now=NOW+timedelta(minutes=1),
            deadline=datetime(2026, 9, 8, 7, 15, tzinfo=JST), aware=runner.aware)


def test_20_buy_unresolved_refuses_but_sell_c3_does_not_reevaluate(monkeypatch, tmp_path):
    buy_home = make_home(tmp_path/'buy')
    crash_at(buy_home, monkeypatch, 'C2')
    other = proposal('open-buy', code='6857')
    with closing(Ledger(buy_home/'ledger.sqlite')) as ledger:
        ledger.create_notice(other, NOW)
        ledger.set_notice_state(other.proposal_id, 'APPROVED', NOW)
        ledger.set_notice_state(other.proposal_id, 'SENT', NOW)
    with pytest.raises(RunError, match='UNRESOLVED_LEDGER'):
        execute(buy_home, now=NOW+timedelta(minutes=1))
    sell_home = make_home(tmp_path/'sell')
    p = proposal(side='SELL')
    crash_at(sell_home, monkeypatch, 'C2', p)
    original = runner_resume.finalize_resume
    monkeypatch.setattr(runner_resume, 'finalize_resume',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('C3')))
    with pytest.raises(RuntimeError): execute(sell_home, p, now=NOW+timedelta(seconds=30))
    monkeypatch.setattr(runner_resume, 'finalize_resume', original)
    with closing(Ledger(sell_home/'ledger.sqlite')) as ledger:
        ledger.create_notice(other, NOW)
        ledger.set_notice_state(other.proposal_id, 'APPROVED', NOW)
        ledger.set_notice_state(other.proposal_id, 'SENT', NOW)
    monkeypatch.setattr(runner, 'evaluate', lambda *a, **k: pytest.fail('C3 reevaluated gate'))
    assert execute(sell_home, p, now=NOW+timedelta(minutes=1))['status'] == 'CANDIDATES'


def test_21_second_candidate_failure_stops_later_processing(monkeypatch, tmp_path):
    home = make_home(tmp_path)
    p1, p2, p3 = (proposal('a', code='6857'), proposal('b', code='7203'),
                  proposal('c', code='6857'))
    calls = 0
    original = Ledger.set_notice_state
    def stop_second(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2: raise RuntimeError('second')
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Ledger, 'set_notice_state', stop_second)
    with pytest.raises(RuntimeError, match='second'):
        run(home, 'resume-run', DAY, [p1, p2, p3], mock_verdicts([p1,p2,p3], 'resume-run', NOW),
            NOW, Valuation(1_060_000,1_060_000))
    monkeypatch.setattr(Ledger, 'set_notice_state', original)
    original_notice = Ledger.notice
    def sent_second(self, pid):
        value = original_notice(self, pid)
        if pid == 'b': value['notice_state'] = 'SENT'
        return value
    monkeypatch.setattr(Ledger, 'notice', sent_second)
    with pytest.raises(RunError, match='OWNERSHIP_UNPROVEN'):
        run(home, 'resume-run', DAY, [p1,p2,p3], mock_verdicts([p1,p2,p3], 'resume-run', NOW),
            NOW+timedelta(minutes=1), Valuation(1_060_000,1_060_000))
    with sqlite3.connect(home/'orchestration.sqlite') as db:
        assert db.execute("SELECT state FROM candidates WHERE pid='a'").fetchone() == ('APPROVED',)
        assert db.execute("SELECT state FROM candidates WHERE pid='b'").fetchone() == ('INTENT',)
        assert db.execute("SELECT state FROM candidates WHERE pid='c'").fetchone() is None


def test_22_resume_records_manifest_and_candidate_without_changing_start(monkeypatch, tmp_path):
    home = make_home(tmp_path)
    crash_at(home, monkeypatch, 'C2')
    before = journal_state(home)[2]['started_at']
    result = execute(home, now=NOW+timedelta(minutes=1))
    candidate, _, manifest = journal_state(home)
    item = json.loads(candidate[1])
    assert manifest['started_at'] == before
    assert manifest['resumes'][0]['from_states'] == {'resume-buy': 'CREATED'}
    assert item['resume'] == result['candidates'][0]['resume']
    assert item['resume']['original_gate']['allowed'] is True


def test_23_diagnosis_changes_from_reconciliation_to_complete(monkeypatch, tmp_path):
    from aitrader.runner_diagnostics import diagnose_mock_run
    home = make_home(tmp_path)
    crash_at(home, monkeypatch, 'C2')
    assert diagnose_mock_run(home, 'resume-run', DAY)['classification'] == 'NEEDS_RECONCILIATION'
    execute(home, now=NOW+timedelta(minutes=1))
    candidate, outbox, manifest = journal_state(home)
    assert candidate[0] == 'APPROVED' and outbox == 1 and len(manifest['resumes']) == 1
    assert diagnose_mock_run(home, 'resume-run', DAY)['classification'] == 'COMPLETE'


def test_24_existing_outbox_key_rolls_back_candidate_and_manifest(monkeypatch, tmp_path):
    home = make_home(tmp_path)
    crash_at(home, monkeypatch, 'C2')
    key = f'{DAY}:resume-buy:{proposal().packet_hash}:CANDIDATE'
    with sqlite3.connect(home/'orchestration.sqlite') as db:
        db.execute('INSERT INTO outbox VALUES(?,?)', [key, '{}'])
    before = journal_state(home)[2]
    with pytest.raises(sqlite3.IntegrityError):
        execute(home, now=NOW+timedelta(minutes=1))
    candidate, _, manifest = journal_state(home)
    assert candidate[0] == 'INTENT' and manifest == before


def test_25_legacy_c2_keeps_old_reconciliation_stop(monkeypatch, tmp_path):
    home = make_home(tmp_path, managed=False)
    crash_at(home, monkeypatch, 'C2')
    with pytest.raises(RunError, match='台帳だけ'):
        execute(home, now=NOW+timedelta(minutes=1))


def test_26_manifest_update_failure_rolls_back_all_three_writes(monkeypatch, tmp_path):
    home = make_home(tmp_path)
    crash_at(home, monkeypatch, 'C2')
    before = journal_state(home)[2]
    real_connect = sqlite3.connect
    class FaultConnection:
        def __init__(self, connection): self.connection = connection
        def execute(self, sql, parameters=()):
            if sql.startswith('UPDATE runs SET manifest='):
                raise RuntimeError('manifest fault')
            return self.connection.execute(sql, parameters)
        def __getattr__(self, name): return getattr(self.connection, name)
    def fault_connect(database, *args, **kwargs):
        connection = real_connect(database, *args, **kwargs)
        return (FaultConnection(connection)
                if str(database).endswith('orchestration.sqlite') else connection)
    monkeypatch.setattr(runner.sqlite3, 'connect', fault_connect)
    with pytest.raises(RuntimeError, match='manifest fault'):
        execute(home, now=NOW+timedelta(minutes=1))
    monkeypatch.setattr(runner.sqlite3, 'connect', real_connect)
    candidate, outbox, manifest = journal_state(home)
    assert candidate[0] == 'INTENT' and outbox == 0 and manifest == before


def test_27_success_result_remains_authoritative_with_old_failure(monkeypatch, tmp_path):
    from aitrader.runner_diagnostics import diagnose_mock_run
    home = make_home(tmp_path)
    crash_at(home, monkeypatch, 'C2')
    failure = home/'runs'/str(DAY)/'resume-run'/'failure.json'
    assert failure.exists()
    result = execute(home, now=NOW+timedelta(minutes=1))
    saved = json.loads((failure.parent/'result.json').read_text(encoding='utf-8'))
    assert saved == result and saved['status'] == 'CANDIDATES'
    assert diagnose_mock_run(home, 'resume-run', DAY)['classification'] == 'COMPLETE'
