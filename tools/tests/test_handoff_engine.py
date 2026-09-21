import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'automation'))
import codex_engine as h


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='handoff-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base/'!!!※ 日本語 & space'
        self.root.mkdir()
        self.runtime = self.base/'runtime'
        for agent in h.FILES:
            self.write(agent,'review '+agent)
        self.calls = 0
        self.engine = h.Engine(self.root,self.runtime,stable=0,runner=self.fake)

    def write(self,agent,summary,ready=True):
        path = self.root/h.FILES[agent]
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('# Test\n\n## A. 固定\nRead only auto tools.\n\n## B. 今回の依頼\n\n```text\n今回の依頼: '+summary+'\nDetails\n```\n\n## History\nold\n',encoding='utf-8')
        if ready:
            h.publish(path)
        return path

    def fake(self,args,prompt,root,folder,timeout):
        self.calls += 1
        self.write('claude','next '+str(self.calls))
        return 'ok'

    def tick(self,dry=False):
        return self.engine.once('codex',dry=dry,exe=sys.executable)

    def ready(self):
        self.assertEqual(self.tick()['status'],'settling')

    def test_dryrun_has_no_state_logs_claim_or_git(self):
        before = {p.relative_to(self.base):p.read_bytes() for p in self.base.rglob('*') if p.is_file()}
        self.assertEqual(self.tick(True)['status'],'dryrun')
        after = {p.relative_to(self.base):p.read_bytes() for p in self.base.rglob('*') if p.is_file()}
        self.assertEqual(before,after)
        self.assertEqual(self.calls,0)

    def test_success_and_restart_never_repeat_hash(self):
        self.ready()
        self.assertEqual(self.tick()['status'],'ok')
        self.engine = h.Engine(self.root,self.runtime,stable=0,runner=self.fake)
        self.assertEqual(self.tick()['status'],'already_processed')
        self.assertEqual(self.calls,1)

    def test_republished_identical_body_does_not_repeat(self):
        self.ready(); self.tick()
        h.publish(self.root/h.FILES['codex'])
        self.assertEqual(self.tick()['status'],'already_processed')

    def test_unpublished_and_changed_draft_are_not_claimed(self):
        path = self.write('codex','draft',False)
        self.assertEqual(self.tick()['status'],'unpublished')
        h.publish(path); self.ready()
        path.write_text(path.read_text(encoding='utf-8').replace('Details','Half edited'),encoding='utf-8')
        self.assertEqual(self.tick()['status'],'unpublished')
        self.assertEqual(self.calls,0)

    def test_history_edits_do_not_retrigger_completed_request(self):
        self.ready(); self.tick()
        path = self.root/h.FILES['codex']
        path.write_text(path.read_text(encoding='utf-8')+'new history\n',encoding='utf-8')
        self.assertEqual(self.tick()['status'],'already_processed')

    def test_parser_rejects_missing_multiple_request_and_unclosed_fence(self):
        path = self.root/h.FILES['codex']
        for text in ('今回の依頼: outside', '## B. request\n今回の依頼: one\n今回の依頼: two\n',
                     '## B. request\n```\n今回の依頼: unfinished\n'):
            path.write_text(text,encoding='utf-8')
            with self.assertRaises(h.ProtocolError): h.read_request(path)

    def test_pending_subsection_is_not_part_of_active_b(self):
        path = self.root/h.FILES['codex']
        path.write_text('## B. active\n今回の依頼: active\n### 保留中\n今回の依頼: old\n',encoding='utf-8')
        self.assertEqual(h.read_request(path).summary,'active')

    def test_question_blocks_even_with_old_mtime(self):
        q = self.root/h.QUESTIONS[1]
        q.write_text('Need answer',encoding='utf-8'); os.utime(q,(1,1))
        self.assertEqual(self.tick()['status'],'question')
        self.assertEqual(self.calls,0)

    def test_global_lock_covers_both_agents(self):
        with h.lock(self.engine.runtime):
            self.assertEqual(self.tick()['status'],'locked')
            self.assertEqual(self.engine.once('claude',exe=sys.executable)['status'],'locked')

    def test_failure_blocks_both_agents_and_does_not_retry(self):
        self.engine.runner = lambda *args:'error'
        self.ready(); self.assertEqual(self.tick()['status'],'error')
        self.assertEqual(self.engine.once('claude',exe=sys.executable)['status'],'blocked')
        self.assertEqual(self.tick()['status'],'blocked')

    def test_unchanged_output_stops_chain(self):
        self.engine.runner = lambda *args:'ok'
        self.ready(); self.assertEqual(self.tick()['status'],'nohandoff')

    def test_self_modified_input_stops_chain(self):
        def runner(*args):
            self.write('codex','self modified')
            self.write('claude','new output')
            return 'ok'
        self.engine.runner=runner
        self.ready(); self.assertEqual(self.tick()['status'],'self_modified')

    def test_daily_limit_shared_and_new_day_resets(self):
        self.engine.max_runs=1
        self.ready(); self.tick()
        self.write('codex','second')
        self.ready(); self.assertEqual(self.tick()['status'],'daily_limit')
        state=h.load_state(self.engine.runtime); state['date']='2000-01-01'; h.save_state(self.engine.runtime,state)
        self.assertEqual(self.tick()['status'],'ok')

    def test_stability_requires_second_observation_and_elapsed_time(self):
        self.engine.stable=60
        self.ready(); self.assertEqual(self.tick()['status'],'settling')
        self.assertEqual(self.calls,0)

    def test_done_and_runtime_inside_checkout_rejected(self):
        self.write('codex','引き渡し不要（完了）')
        self.assertEqual(self.tick()['status'],'done')
        with self.assertRaises(h.ProtocolError): h.Engine(self.root,self.root/'logs')

    def test_local_child_receives_literal_unicode_stdin_and_arguments(self):
        text='日本語 ! & % $(echo bad)\nsecond line'
        literal='a & b ! space'
        folder=self.base/'process'
        args=[sys.executable,'-c','import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()); print(sys.argv[1])',literal]
        self.assertEqual(h.run_process(args,text,self.root,folder,10),'ok')
        self.assertEqual((folder/'stdout.txt').read_text(encoding='utf-8'),text+literal+'\n')

    def test_timeout_stops_local_child(self):
        folder=self.base/'timeout'
        self.assertEqual(h.run_process([sys.executable,'-c','import time; time.sleep(30)'],'',self.root,folder,.2),'timeout')

    def test_corrupt_state_fails_closed(self):
        self.engine.runtime.mkdir(parents=True)
        (self.engine.runtime/'state.json').write_text('broken',encoding='utf-8')
        with self.assertRaises(ValueError): self.tick()

    def test_alternation_uses_one_budget_and_stops_on_done(self):
        self.ready(); self.assertEqual(self.tick()['status'],'ok')
        def finish(*args):
            self.write('codex','引き渡し不要（complete）')
            return 'ok'
        self.engine.runner=finish
        self.assertEqual(self.engine.once('claude',exe=sys.executable)['status'],'settling')
        self.assertEqual(self.engine.once('claude',exe=sys.executable)['status'],'done')
        self.assertEqual(h.load_state(self.engine.runtime)['runs'],2)

    def test_crash_keeps_running_claim_and_blocks_later_work(self):
        def crash(*args): raise SystemExit(8)
        self.engine.runner=crash
        self.ready()
        with self.assertRaises(SystemExit): self.tick()
        self.assertEqual(self.tick()['status'],'blocked')

    def test_failed_agent_cannot_release_published_output(self):
        def fail(*args):
            self.write('claude','premature published work')
            return 'error'
        self.engine.runner=fail
        self.ready(); self.assertEqual(self.tick()['status'],'error')
        self.assertEqual(self.engine.once('claude',exe=sys.executable)['status'],'blocked')

    @unittest.skipUnless(os.name=='nt','Windows process-tree termination')
    def test_timeout_terminates_descendant(self):
        marker=self.base/'child-survived'
        folder=self.base/'tree'
        child='import time; from pathlib import Path; time.sleep(1); Path('+repr(str(marker))+').write_text("alive")'
        parent='import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",'+repr(child)+']); time.sleep(30)'
        self.assertEqual(h.run_process([sys.executable,'-c',parent],'',self.root,folder,.3),'timeout')
        time.sleep(1.1)
        self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main()
