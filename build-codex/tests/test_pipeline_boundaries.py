"""Offline malformed-input checks before durable pipeline side effects."""
from datetime import timedelta
import pytest
from test_runner import setup, NOW, DAY
from test_notification_plan import SETTINGS, CONTEXTS, offline
from test_review_runner import inputs
from aitrader.runner import RunError, Valuation
from aitrader.review_runner import run_reviewed_mock
from aitrader.notification_plan import prepare_notifications
from aitrader.notification_queue import enqueue_prepared_notifications


def files(home):
    return {str(p.relative_to(home)): p.read_bytes() for p in home.rglob('*') if p.is_file()}


@pytest.mark.parametrize('run_id', ['../escape', 'C:/escape', None, 12])
def test_bad_review_run_id_is_public_validation_without_writes(setup, run_id):
    home, _ = setup
    p, responses = inputs()
    before = files(home)
    with pytest.raises(RunError):
        run_reviewed_mock(home, run_id, DAY, [p], responses, NOW,
                          Valuation(1000000, 1000000),
                          started_at=NOW-timedelta(seconds=1), market_context={})
    assert files(home) == before


@pytest.mark.parametrize('execution_day', [NOW, DAY.isoformat(), None])
def test_bad_plan_date_rejected_before_lock_file_creation(setup, execution_day):
    home, _ = setup
    before = files(home)
    with pytest.raises(RunError):
        prepare_notifications(home, 'run1', execution_day,
                              contexts=CONTEXTS, settings=SETTINGS)
    assert files(home) == before


@pytest.mark.parametrize('bad_now', [NOW.replace(tzinfo=None), None, True])
def test_queue_bad_time_rejected_without_database_creation(setup, bad_now):
    home, _ = setup
    before = files(home)
    with pytest.raises(RunError):
        enqueue_prepared_notifications(home, 'run1', DAY, now=bad_now,
                                       contexts=CONTEXTS, settings=SETTINGS)
    assert files(home) == before


@pytest.mark.parametrize('run_id', ['../escape', 'C:/escape'])
def test_queue_path_traversal_rejected_without_writes(setup, run_id):
    home, _ = setup
    before = files(home)
    with pytest.raises(RunError):
        enqueue_prepared_notifications(home, run_id, DAY, now=NOW,
                                       contexts=CONTEXTS, settings=SETTINGS)
    assert files(home) == before
