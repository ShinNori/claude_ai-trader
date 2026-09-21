"""Offline read-only HTML must not promote uncertain or rejected candidates."""
import re
import sqlite3
from contextlib import closing
from html.parser import HTMLParser

import pytest
from test_runner import setup, DAY, proposal
from test_runner_diagnostics import hashes
from test_notification_plan import offline


class Parsed(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags=[];self.text=[];self.attrs=[]
        self.feed(html)
    def handle_starttag(self,tag,attrs):
        self.tags.append(tag);self.attrs.extend(attrs)
    def handle_data(self,data):
        self.text.append(data)


def render(home):
    from aitrader.mock_report import render_mock_report
    before=hashes(home)
    html=render_mock_report(home,'run1',DAY)
    assert hashes(home)==before
    assert isinstance(html,str)
    parsed=Parsed(html)
    assert 'script' not in parsed.tags
    assert not any(k.lower().startswith('on') for k,v in parsed.attrs)
    assert not any(k in ('src','href','action') and re.match(r'(?i)(https?:|//|javascript:)',v or '')
                   for k,v in parsed.attrs)
    assert '模擬' in ''.join(parsed.text)
    return html,parsed


def test_approved_mock_signal_conditions_visible(setup):
    home,execute=setup
    execute()
    _,parsed=render(home)
    text=' '.join(parsed.text)
    assert '6857' in text
    assert 'BUY' in text or '買' in text
    assert '100' in text
    assert '1000' in text or '1,000' in text


def test_rejected_has_no_candidate_price_table(setup):
    home,execute=setup
    execute(decisions={'codex':'REJECT'})
    _,parsed=render(home)
    assert 'td' not in parsed.tags


def test_real_xss_proposal_not_executable(setup):
    home,execute=setup
    execute(ps=[proposal(reason='<script>alert(1)</script><img src=x onerror=alert(1)>')])
    _,parsed=render(home)
    assert 'script' not in parsed.tags
    assert 'img' not in parsed.tags


@pytest.mark.parametrize('artifact',['result','manifest','gate_results'])
def test_tampered_source_does_not_display_signal(setup,artifact):
    home,execute=setup
    execute()
    (home/'runs'/str(DAY)/'run1'/f'{artifact}.json').write_text('{}',encoding='utf-8')
    _,parsed=render(home)
    assert 'td' not in parsed.tags


def test_live_wal_holds_signal_without_side_effects(setup):
    home,execute=setup
    execute()
    with closing(sqlite3.connect(home/'orchestration.sqlite')) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute("UPDATE candidates SET state='INTENT' WHERE pid='buy1'")
        writer.commit()
        assert (home/'orchestration.sqlite-wal').exists()
        _,parsed=render(home)
        assert 'td' not in parsed.tags
