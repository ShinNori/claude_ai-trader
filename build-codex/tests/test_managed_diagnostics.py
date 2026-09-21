"""Read-only runner diagnostics across explicit managed and legacy homes."""
from contextlib import closing
from datetime import timedelta
import hashlib
import json
import sqlite3

import pytest

from aitrader.runner import initialize_mock
from aitrader.runner_diagnostics import diagnose_mock_run
from aitrader_ops.models import PositionIn
from aitrader_ops.notify import Notifier
from test_managed_stop_runner import DAY, NOW, SETTINGS, execute, market
from aitrader.managed_stop import initialize_managed_mock


def fingerprints(home):
    return {str(path.relative_to(home)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in home.rglob('*') if path.is_file()}


@pytest.fixture(autouse=True)
def no_delivery(monkeypatch):
    monkeypatch.setattr(
        Notifier, 'flush',
        lambda *args, **kwargs: pytest.fail('diagnostics attempted delivery'))


@pytest.fixture
def completed_managed(tmp_path):
    home = tmp_path/'managed-diagnostic'
    initialize_managed_mock(
        home, 1_000_000, [PositionIn('6857', 200, 100.0)],
        NOW-timedelta(days=1), settings=SETTINGS)
    market(home)
    execute(home, run_id='managed-diagnostic')
    return home


def diagnose_unchanged(home, run_id='managed-diagnostic'):
    before = fingerprints(home)
    report = diagnose_mock_run(home, run_id, DAY)
    assert fingerprints(home) == before
    assert report['automatic_resume_allowed'] is False
    assert report['repaired'] is False
    return report


def test_completed_managed_run_is_complete_and_read_only(completed_managed):
    assert diagnose_unchanged(completed_managed)['classification'] == 'COMPLETE'


def test_changed_fixed_policy_is_not_accepted_as_original(completed_managed):
    marker_path = completed_managed/'mock-runner.json'
    marker = json.loads(marker_path.read_text(encoding='utf-8'))
    marker['stop_files'].append(str((completed_managed/'SECOND_STOP').resolve()))
    marker_path.write_text(json.dumps(marker), encoding='utf-8')

    report = diagnose_unchanged(completed_managed)
    assert report['classification'] == 'CONFLICT'
    assert 'ORIGINAL_OR_ARTIFACT_MISMATCH' in report['reasons']


def test_manifest_without_managed_policy_is_rejected(completed_managed):
    database = completed_managed/'orchestration.sqlite'
    with closing(sqlite3.connect(database)) as connection:
        raw = connection.execute(
            'SELECT manifest FROM runs WHERE id=?', ['managed-diagnostic']).fetchone()[0]
        manifest = json.loads(raw)
        manifest.pop('managed_stop_policy')
        connection.execute(
            'UPDATE runs SET manifest=? WHERE id=?',
            [json.dumps(manifest), 'managed-diagnostic'])
        connection.commit()

    report = diagnose_unchanged(completed_managed)
    assert report['classification'] == 'CONFLICT'
    assert 'ORIGINAL_OR_ARTIFACT_MISMATCH' in report['reasons']


def test_missing_database_is_not_recreated(completed_managed):
    target = completed_managed/'ledger.sqlite'
    target.unlink()

    report = diagnose_unchanged(completed_managed)
    assert report['classification'] == 'MISSING'
    assert 'REQUIRED_DATABASE_MISSING' in report['reasons']
    assert not target.exists()


def test_managed_wal_is_refused_without_reading_stale_complete(completed_managed):
    database = completed_managed/'orchestration.sqlite'
    with closing(sqlite3.connect(database)) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute(
            "UPDATE candidates SET owner='concurrent-change' WHERE pid='buy-managed'")
        writer.commit()
        assert (completed_managed/'orchestration.sqlite-wal').stat().st_size > 0
        report = diagnose_unchanged(completed_managed)

    assert report['classification'] == 'NEEDS_RECONCILIATION'
    assert report['reasons'] == ['LIVE_DATABASE_UNSUPPORTED']
    assert report['candidates'] == []


def test_exact_legacy_v1_home_remains_supported(tmp_path):
    home = tmp_path/'legacy-diagnostic'
    initialize_mock(home, 1_000_000, [], NOW-timedelta(days=1))
    market(home)
    execute(home, run_id='legacy-diagnostic')

    assert diagnose_unchanged(home, 'legacy-diagnostic')['classification'] == 'COMPLETE'
