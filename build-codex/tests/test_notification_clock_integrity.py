"""Only a fully verified operation may advance the durable notification clock."""
import copy
import sqlite3
from contextlib import closing
from datetime import timedelta

import pytest
from test_notification_queue import setup, prepared, queue, CONFIG, NOW, DAY, CONTEXTS, offline
from test_mock_delivery import deliver
from aitrader.runner import RunError


@pytest.mark.parametrize('invalid',['settings','artifact','queue'])
def test_rejected_future_operation_does_not_poison_clock(setup,invalid):
    home,plan=prepared(setup)
    queue(home)
    changes={}
    path=home/'runs'/str(DAY)/'run1'/'notification_plan.json'
    original=path.read_bytes()
    saved_row=None
    if invalid=='settings':
        changed=copy.deepcopy(CONFIG)
        changed['line']['allowed_user_id']='wrong'
        changes['settings']=changed
    elif invalid=='artifact':
        path.write_text('{}',encoding='utf-8')
    else:
        with closing(sqlite3.connect(home/'notification.sqlite')) as con:
            saved_row=con.execute('SELECT * FROM outbox').fetchone()
            con.execute('DELETE FROM outbox')
            con.commit()
    with pytest.raises((RunError,ValueError)):
        deliver(home,now=NOW+timedelta(minutes=2),**changes)
    if invalid=='artifact':
        path.write_bytes(original)
    elif invalid=='queue':
        with closing(sqlite3.connect(home/'notification.sqlite')) as con:
            con.execute('INSERT INTO outbox VALUES ('+','.join('?' for _ in saved_row)+')',saved_row)
            con.commit()
    # Successful call below the rejected future time must still be permitted.
    assert deliver(home,now=NOW+timedelta(minutes=1))['simulated_sent_by_this_call'] is True


def test_same_time_remains_valid_with_durable_clock(setup):
    home,_=prepared(setup)
    queue(home)
    first=deliver(home,now=NOW)
    again=deliver(home,now=NOW)
    assert first['simulated_sent_by_this_call'] is True
    assert again['attempted_by_this_call'] is False


def test_old_database_without_clock_keeps_report_lower_bound(setup):
    home,_=prepared(setup)
    queue(home)
    (home/'STOP').write_text('synthetic',encoding='utf-8')
    later=NOW+timedelta(minutes=2)
    deliver(home,now=later)
    with closing(sqlite3.connect(home/'notification-plans.sqlite')) as con:
        con.execute('DROP TABLE notification_clock')
        con.commit()
    with pytest.raises(RunError):
        deliver(home,now=NOW+timedelta(minutes=1))
    assert deliver(home,now=later)['attempted_by_this_call'] is False
    with closing(sqlite3.connect(home/'notification-plans.sqlite')) as con:
        assert con.execute('SELECT observed_at FROM notification_clock WHERE run_id=?',['run1']).fetchone()[0]==later.isoformat()


@pytest.mark.parametrize('invalid',['not-a-date','2026-09-08T07:10:00'])
def test_invalid_durable_clock_refused(setup,invalid):
    home,_=prepared(setup)
    queue(home)
    with closing(sqlite3.connect(home/'notification-plans.sqlite')) as con:
        con.execute('UPDATE notification_clock SET observed_at=? WHERE run_id=?',[invalid,'run1'])
        con.commit()
    with pytest.raises((RunError,ValueError)):
        deliver(home,now=NOW+timedelta(minutes=1))
    with closing(sqlite3.connect(home/'notification.sqlite')) as con:
        assert con.execute('SELECT attempts FROM outbox').fetchone()[0]=='[]'


def test_other_run_clock_untouched_and_not_used_as_lower_bound(setup):
    home,_=prepared(setup)
    queue(home)
    future=(NOW+timedelta(days=1)).isoformat()
    with closing(sqlite3.connect(home/'notification-plans.sqlite')) as con:
        con.execute('INSERT INTO notification_clock VALUES(?,?)',['other-run',future])
        con.commit()
    assert deliver(home,now=NOW)['simulated_sent_by_this_call'] is True
    with closing(sqlite3.connect(home/'notification-plans.sqlite')) as con:
        assert con.execute('SELECT observed_at FROM notification_clock WHERE run_id=?',['other-run']).fetchone()[0]==future
