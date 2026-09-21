"""Missing later ledger references do not partially reconcile earlier rows."""
from contextlib import closing
from dataclasses import replace

import pytest

from aitrader_ops.ledger import Ledger
from aitrader_ops.notify import Notifier
from test_v041_order4_review_claude import NOW, SETTINGS, proposal


def test_later_missing_notice_does_not_reconcile_first(tmp_path, monkeypatch):
    with closing(Ledger(tmp_path/'ledger.sqlite')) as ledger:
        ledger.init_snapshot(1_000_000, [], [], NOW.replace(hour=6))
        items = [proposal(), replace(proposal(), proposal_id='second')]
        with closing(Notifier(ledger=ledger, state_path=tmp_path/'notify.sqlite',
                              settings=SETTINGS, transport='stub')) as notifier:
            for item in items:
                ledger.create_notice(item, at=NOW)
                ledger.set_notice_state(item.proposal_id, 'APPROVED', NOW)
                notifier.enqueue(key=item.proposal_id, kind='NEW',
                                 message={'type': 'text', 'text': 'fixture'},
                                 proposal_id=item.proposal_id, now=NOW)
            notifier._con.execute("UPDATE outbox SET state='SENT'")
            original = ledger.notice
            def missing(pid):
                if pid == 'second':
                    raise KeyError(pid)
                return original(pid)
            monkeypatch.setattr(ledger, 'notice', missing)
            before = ledger.seq()
            with pytest.raises(ValueError, match='台帳に候補'):
                notifier.reconcile_sent(now=NOW)
            assert ledger.seq() == before
            assert original(items[0].proposal_id)['notice_state'] == 'APPROVED'
            monkeypatch.setattr(ledger, 'notice', original)
            assert notifier.reconcile_sent(now=NOW) == [p.proposal_id for p in items]
            assert notifier.reconcile_sent(now=NOW) == []
