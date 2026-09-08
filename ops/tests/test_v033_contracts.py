"""G07 write rejection and a real CLI invocation on the host filesystem."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'ops'), str(ROOT/'build-codex'), str(ROOT/'common/tests/phase2')]
from test_ops_rereview import make_ledger, AT
from aitrader_ops.ledger import Ledger, LedgerError, _State


def test_h01_new_ledger_rejects_marker_without_changing_history(make_ledger):
    """旧規則区間のない新台帳へのマーカー書込みを拒否し、再起動できる。"""
    ledger = make_ledger()
    last, before = ledger.seq(), ledger.view()
    with pytest.raises(LedgerError):
        ledger._commit('POLICY_UPGRADE', dict(schema_version=3, legacy_last_seq=last, at=AT), AT)
    assert ledger.seq() == last and ledger.replay_known(last) == before
    reopened = Ledger(ledger.path)
    try:
        assert reopened.view() == before
    finally:
        reopened.close()


def test_h02_state_allows_first_legacy_upgrade_only():
    """旧規則からの正規の初回移行は許可し、状態再生でも二回目を拒否する。"""
    state = _State()
    state.rule_version = 2
    state.apply('SNAPSHOT', dict(cash=1000000, positions=[], open_orders=[], at=AT.isoformat()))
    marker = dict(schema_version=3, legacy_last_seq=1, at=AT.isoformat())
    state.apply('POLICY_UPGRADE', marker)
    assert state.rule_version == 3
    with pytest.raises(LedgerError):
        state.apply('POLICY_UPGRADE', marker)


def test_h03_host_cli_check_unicode_copy_is_readonly(tmp_path):
    """日本語・感嘆符・空白を含む実パスでCLIを起動し、台帳本体不変・移行出力なしを確認する。"""
    directory = tmp_path/'!!!※則光用 台帳 検査'
    directory.mkdir()
    copy = directory/'照合用 合成コピー.sqlite'
    ledger = Ledger(copy)
    ledger.init_snapshot(1000000, [], [], AT)
    ledger.close()
    before = hashlib.sha256(copy.read_bytes()).hexdigest()
    names = sorted(p.name for p in directory.iterdir())
    env = os.environ.copy()
    env['PYTHONPATH'] = os.pathsep.join(str(p) for p in sys.path if p)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    command = [sys.executable, '-m', 'aitrader_ops.migrate', '--check', str(copy)]
    result = subprocess.run(command, cwd=ROOT/'ops', env=env, capture_output=True,
                            encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload['eligible'] and payload['violation'] is None
    assert payload['last_seq'] == 1 and not payload['already_upgraded']
    assert payload['legacy_balance'] == payload['current_balance']
    assert hashlib.sha256(copy.read_bytes()).hexdigest() == before
    # SQLite mode=ro can create WAL/SHM reader sidecars even without DB writes.
    after_names = sorted(p.name for p in directory.iterdir())
    assert set(after_names) - set(names) <= {copy.name+'-wal', copy.name+'-shm'}
    print('\nHOST_CLI_CHECK ' + json.dumps(dict(platform=sys.platform, command=command,
          result=payload, sha256=before, unchanged=True, files_after=after_names), ensure_ascii=False))
