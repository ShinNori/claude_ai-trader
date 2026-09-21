"""Every JSON-serializable sequence must preserve independent review."""
import json

import pytest

from aitrader.packet import build_proposals, render_packet
from test_packet_local import args


@pytest.mark.parametrize('key', [
    'verdict', 'verdicts', 'claude_verdict', 'codex_verdict',
    'judge_results', 'other_judge', 'CLAUDE_VERDICT',
])
def test_judgment_inside_nested_tuple_is_rejected(key):
    proposal = build_proposals(**args())[0]
    context = {'news': ([{'nested': ({key: 'APPROVE'},)}],)}
    with pytest.raises(ValueError, match='他AI'):
        render_packet(proposal, context)


def test_plain_tuple_reference_keeps_json_array_compatibility():
    proposal = build_proposals(**args())[0]
    tuple_context = {'news': ({'title': '決算資料'}, '説明')}
    list_context = {'news': [{'title': '決算資料'}, '説明']}
    assert render_packet(proposal, tuple_context) == render_packet(proposal, list_context)
    rendered = json.loads(render_packet(proposal, tuple_context))
    assert rendered['reference'].startswith('<reference>')
    assert tuple_context == {'news': ({'title': '決算資料'}, '説明')}
