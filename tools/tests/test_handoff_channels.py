"""dev チャネル（投資本体の開発ループ: Codex引き渡しプロンプト.md ⇄ build-codex/Claude引き渡しプロンプト.md）の試験。
2026-09-09 Claude 追加。auto チャネルの既存試験（test_handoff_engine.py）は変更しない。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'automation'))
import codex_engine as h  # noqa: E402


class DevChannelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='handoff-dev-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base/'ai-trader ※ 日本語'
        self.root.mkdir()
        self.runtime = self.base/'runtime'
        self.calls = []
        for ch in h.CHANNELS:
            for agent in ('codex', 'claude'):
                self.write(ch, agent, f'{ch} {agent} 初期')
        self.dev = h.Engine(self.root, self.runtime, stable=0, runner=self.fake, channel='dev')
        self.auto = h.Engine(self.root, self.runtime, stable=0, runner=self.fake, channel='auto')

    def write(self, channel, agent, summary, ready=True, extra=''):
        path = self.root/h.CHANNELS[channel]['files'][agent]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# 引き渡し\n\n## A. 固定プロンプト\n規則\n\n## B. 今回の依頼\n\n```\n今回の依頼: '+summary+'\n本文\n```\n'
                        + extra + '\n---\n\n## 過去の依頼\n| 日時 | 依頼 |\n', encoding='utf-8')
        if ready:
            h.publish(path)
        return path

    def fake(self, args, prompt, root, folder, timeout):
        self.calls.append(prompt)
        # 起動された側（prompt の宛先で判定）が相手宛て B を更新して公開する
        channel = 'dev' if 'dev チャネル' in prompt else 'auto'
        other = 'claude' if 'Codex引き渡しプロンプト.md を読み' in prompt or 'Codex引き渡しプロンプト.md のA' in prompt else 'codex'
        self.write(channel, other, f'{channel} 次の依頼 {len(self.calls)}')
        return 'ok'

    def test_dev_files_and_questions_are_separate_from_auto(self):
        self.assertEqual(self.dev.files['codex'], 'Codex引き渡しプロンプト.md')
        self.assertEqual(self.dev.files['claude'], 'build-codex/Claude引き渡しプロンプト.md')
        self.assertEqual(self.dev.questions, ('build-codex/QUESTIONS.md', 'ops/QUESTIONS.md'))
        self.assertEqual(h.FILES, h.CHANNELS['auto']['files'])

    def test_dev_run_uses_dev_prompt_and_updates_dev_claude_file(self):
        self.assertEqual(self.dev.once('codex', exe=sys.executable)['status'], 'settling')
        result = self.dev.once('codex', exe=sys.executable)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['channel'], 'dev')
        prompt = self.calls[-1]
        self.assertIn('「A. 固定プロンプト」と以下に固定した「B. 今回の依頼」に従い、dev codex 初期 を行ってください', prompt)
        self.assertIn('publish --channel dev --agent claude', prompt)
        self.assertIn('build-codex/QUESTIONS.md', prompt)
        self.assertNotIn('tools/automation/QUESTIONS_codex.md', prompt)
        self.assertEqual(h.read_request(self.root/'build-codex/Claude引き渡しプロンプト.md').summary, 'dev 次の依頼 1')
        self.assertEqual(h.read_request(self.root/h.FILES['claude']).summary, 'auto claude 初期')   # auto 側は不変

    def test_pending_section_under_hash3_is_not_part_of_b(self):
        path = self.write('dev', 'codex', '本命', ready=False, extra='\n### 保留中の依頼\n\n```\n今回の依頼: 保留のほう\n```\n')
        self.assertEqual(h.read_request(path).summary, '本命')

    def test_state_keys_are_channel_scoped_and_lock_is_shared(self):
        self.assertEqual(self.dev.once('codex', exe=sys.executable)['status'], 'settling')
        self.assertEqual(self.dev.once('codex', exe=sys.executable)['status'], 'ok')
        state = json.loads((self.runtime/'automation-handoff').glob('*/state.json').__next__().read_text(encoding='utf-8'))
        self.assertTrue(any(k.startswith('dev:codex:') for k in state['processed']))
        self.assertFalse(any(k.startswith('auto:') for k in state['processed']))
        self.assertEqual(self.auto.runtime, self.dev.runtime)      # 同じ runtime → 同じ project.lock
        # auto 側は dev の処理済みに影響されず独立に動く
        self.assertEqual(self.auto.once('codex', exe=sys.executable)['status'], 'settling')
        self.assertEqual(self.auto.once('codex', exe=sys.executable)['status'], 'ok')
        self.assertEqual(len(self.calls), 2)

    def test_dev_question_file_blocks_only_dev(self):
        q = self.root/'ops/QUESTIONS.md'
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_text('人間への質問\n', encoding='utf-8')
        self.assertEqual(self.dev.once('codex', exe=sys.executable)['status'], 'question')
        self.assertEqual(self.auto.once('codex', exe=sys.executable)['status'], 'settling')
        q.write_text('人間への質問\n回答\n<!-- handoff-questions: resolved -->\n', encoding='utf-8')
        self.assertEqual(self.dev.once('codex', exe=sys.executable)['status'], 'settling')

    def test_blocked_state_is_global_across_channels(self):
        def failing(args, prompt, root, folder, timeout):
            return 'error'
        dev = h.Engine(self.root, self.runtime, stable=0, runner=failing, channel='dev')
        self.assertEqual(dev.once('codex', exe=sys.executable)['status'], 'settling')
        self.assertEqual(dev.once('codex', exe=sys.executable)['status'], 'error')
        self.assertEqual(self.auto.once('codex', exe=sys.executable)['status'], 'blocked')

    def test_unknown_channel_rejected(self):
        with self.assertRaises(h.ProtocolError):
            h.Engine(self.root, self.runtime, channel='ops')


    # ---- 2026-09-09 09:15 追加: C04（見出し深さ）、watch_status.json、複数チャネル watch の質問待ち ----

    def test_c04_deeper_headings_end_section_b(self):
        path = self.write('dev', 'codex', '本命', ready=False, extra='\n#### 補足（4 段見出し）\n\n```\n今回の依頼: 補足のほう\n```\n')
        self.assertEqual(h.read_request(path).summary, '本命')

    def test_status_file_written_by_multi_channel_watch_once_and_question_does_not_stop_other_channel(self):
        q = self.root/'ops/QUESTIONS.md'
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_text('人間への質問\n', encoding='utf-8')
        code = h.main(['watch', '--root', str(self.root), '--runtime', str(self.runtime), '--channel', 'auto,dev',
                       '--agent', 'codex', '--once', '--stable', '0'])
        self.assertEqual(code, 1)                                     # --once は質問待ちを 1 で返す
        status = json.loads((self.root/h.STATUS_FILE).read_text(encoding='utf-8'))
        self.assertEqual(status['agent'], 'codex')
        self.assertEqual(status['channels']['dev']['status'], 'question')
        self.assertIn(status['channels']['auto']['status'], ('settling', 'already_processed'))   # auto は dev の質問で止まらず確認された
        self.assertIsNone(status['blocked'])
        self.assertIn('written_at', status)

    def test_no_status_file_flag_and_dry_run_write_nothing(self):
        for extra in (['--no-status-file'], ['--dry-run']):
            h.main(['watch', '--root', str(self.root), '--runtime', str(self.runtime), '--channel', 'auto,dev',
                    '--agent', 'codex', '--once', '--stable', '0'] + extra)
            self.assertFalse((self.root/h.STATUS_FILE).exists())


if __name__ == '__main__':
    unittest.main()
