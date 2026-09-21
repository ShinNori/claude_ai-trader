"""Codex regression review: no real model calls or scheduled task registration."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

import test_handoff_channels as fixtures
import codex_engine as h


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.DevChannelTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def main(self, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            return h.main([*args, '--root', str(self.f.root), '--runtime', str(self.f.runtime)])

    def test_fenced_b_examples_and_long_fences_are_not_protocol(self):
        path = self.f.root/h.FILES['codex']
        path.write_text('````text\n## B. example\n```\n````\n## B. actual\n今回の依頼: actual\n'
                        '~~~text\n#### example\n~~~\n#### pending\n今回の依頼: later\n', encoding='utf-8')
        h.publish(path)
        result = h.read_request(path)
        self.assertEqual(result.summary, 'actual')
        self.assertTrue(result.ready)
        self.assertNotIn('later', result.section)

    def test_marker_in_code_is_not_publication(self):
        path = self.f.root/h.FILES['codex']
        path.write_text('## B. actual\n今回の依頼: actual\n```\n<!-- handoff-ready: '+'0'*64+' -->\n```\n', encoding='utf-8')
        self.assertFalse(h.read_request(path).ready)
        h.publish(path)
        result = h.read_request(path)
        self.assertTrue(result.ready)
        self.assertIn('0'*64, result.section)  # Example remains hashed content.

    def test_state_counter_and_observation_corruption_stop_before_launch(self):
        runtime = self.f.auto.runtime
        runtime.mkdir(parents=True)
        base = h.load_state(runtime)
        for key, value in [('runs', -1), ('runs', '0'), ('runs', True), ('date', 'bad'),
                           ('blocked', []), ('processed', {'x': []}),
                           ('observations', {'codex': {'signature':'x','since':float('nan')}})]:
            with self.subTest(key=key, value=value):
                h.save_state(runtime, dict(base, **{key:value}))
                with self.assertRaises((h.ProtocolError, ValueError)):
                    self.f.auto.once('codex', exe=sys.executable)
        self.assertEqual(self.f.calls, [])

    def test_preview_validates_explicit_cli_and_never_claims_work(self):
        for exe in [str(self.f.base/'missing.exe'), str(self.f.base/'wrapper.cmd')]:
            result = self.f.dev.once('codex', dry=True, exe=exe)
            self.assertFalse(result['cli_found'])
            self.assertTrue(result['cli_error'])
        self.assertFalse(self.f.dev.runtime.exists())

    def test_resume_checks_dev_questions_even_with_default_auto(self):
        runtime = self.f.dev.runtime
        runtime.mkdir(parents=True)
        state = h.load_state(runtime)
        state['blocked'] = 'error:dev:codex:hash'
        h.save_state(runtime, state)
        (self.f.root/'ops').mkdir(exist_ok=True)
        (self.f.root/'ops/QUESTIONS.md').write_text('pending', encoding='utf-8')
        before = (runtime/'state.json').read_bytes()
        with self.assertRaises(h.ProtocolError):
            self.main('resume')
        self.assertEqual((runtime/'state.json').read_bytes(), before)
        self.assertEqual(self.main('resume','--dry-run'), 0)
        self.assertEqual((runtime/'state.json').read_bytes(), before)

    def test_resume_preview_creates_no_runtime(self):
        self.assertEqual(self.main('resume','--dry-run'), 0)
        self.assertFalse(self.f.auto.runtime.exists())

    def test_once_question_is_not_hidden_by_later_idle_channel(self):
        (self.f.root/h.QUESTIONS[0]).write_text('pending', encoding='utf-8')
        self.assertEqual(self.main('watch','--channel','auto,dev','--once','--no-status-file'), 1)
        self.assertEqual(self.f.calls, [])

    def test_actual_done_run_does_not_start_second_channel_in_same_tick(self):
        with patch.object(h.Engine, 'once', return_value={'status':'done','log':'synthetic-run'}) as once:
            self.assertEqual(self.main('watch','--channel','auto,dev','--once','--no-status-file'), 0)
        self.assertEqual(once.call_count, 1)

    def test_idle_done_channel_still_checks_other_channel(self):
        with patch.object(h.Engine, 'once', side_effect=[{'status':'done'},{'status':'settling'}]) as once:
            self.assertEqual(self.main('watch','--channel','auto,dev','--once','--no-status-file'), 0)
        self.assertEqual(once.call_count, 2)

    def test_heartbeat_refreshes_during_a_long_turn_and_stops_afterwards(self):
        observed, refreshed = [], threading.Event()
        def capture(root, payload):
            observed.append(payload)
            if len(observed) >= 2:
                refreshed.set()
        with patch.object(h, 'write_status', side_effect=capture):
            with h.status_heartbeat(self.f.root, {'agent':'codex'}, .01):
                self.assertTrue(refreshed.wait(2))
            count = len(observed)
            self.assertGreaterEqual(count, 2)
        self.assertTrue(all(row['phase'] == 'checking_or_running' for row in observed))

    def test_status_files_do_not_collide_between_agents(self):
        h.write_status(self.f.root, {'agent':'codex', 'phase':'waiting'})
        before = (self.f.root/h.STATUS_FILE).read_bytes()
        h.write_status(self.f.root, {'agent':'claude', 'phase':'waiting'})
        self.assertEqual((self.f.root/h.STATUS_FILE).read_bytes(), before)
        self.assertTrue((self.f.root/'tools/automation/watch_status_claude.json').exists())

    def test_status_contains_no_prompt_summary_or_runtime_path(self):
        with patch.object(h.Engine, 'once', return_value={'status':'ok','log':'private/path', 'summary':'private instructions'}):
            self.main('watch','--once')
        value = (self.f.root/h.STATUS_FILE).read_text(encoding='utf-8')
        self.assertNotIn('private', value)
        self.assertNotIn('runtime', value)

    def test_pingpong_dispatches_both_agents_and_stops_at_one_round(self):
        agents = []
        def once(engine, agent, *args):
            agents.append(agent)
            return {'status':'ok','log':'synthetic-run'}
        with patch.object(h.Engine,'once',once), patch.object(h.time,'sleep'):
            self.assertEqual(self.main('pingpong','--channel','dev','--rounds','1'), 0)
        self.assertEqual(agents, ['codex','claude'])

    def test_dev_prompt_prohibits_self_resolving_questions(self):
        request = h.read_request(self.f.root/h.CHANNELS['dev']['files']['codex'])
        self.assertIn('解決マーカーを自分で追加しない', h.prompt_for('codex', request, 'dev'))


if __name__ == '__main__':
    unittest.main()
