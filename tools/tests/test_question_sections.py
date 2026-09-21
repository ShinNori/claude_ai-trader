import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'automation'))
import codex_engine as h

class QuestionSections(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'root'; self.root.mkdir()
        self.q=self.root/h.QUESTIONS[0]; self.q.parent.mkdir(parents=True)
        self.old='a'*64; self.new='b'*64
    def block(self, name, key, resolved=False):
        return '## '+name+'\n依頼hash: `'+key+'`\nNeed answer\n'+('<!-- handoff-questions: resolved -->\n' if resolved else '')
    def put(self,text): self.q.write_text(text,encoding='utf-8')
    def test_old_resolution_does_not_resolve_new_question(self):
        self.put('# Questions\n'+self.block('old',self.old,True)+self.block('new',self.new))
        self.assertEqual(h.unresolved_questions(self.root),[{'file':h.QUESTIONS[0],'heading':'new','hash':self.new}])
    def test_unrelated_hash_still_requires_an_answer(self):
        self.put(self.block('old',self.old)+self.block('new',self.new,True))
        self.assertEqual(h.unresolved_questions(self.root)[0]['hash'],self.old)
    def test_trailing_marker_only_resolves_last_section(self):
        self.put(self.block('old',self.old)+self.block('new',self.new)+'<!-- handoff-questions: resolved -->\n')
        self.assertEqual(len(h.unresolved_questions(self.root)),1)
        self.assertEqual(h.unresolved_questions(self.root)[0]['hash'],self.old)
    def test_standalone_marker_does_not_authorize_future_question(self):
        self.put('<!-- handoff-questions: resolved -->\n')
        self.assertEqual(h.unresolved_questions(self.root),[])
        self.put('<!-- handoff-questions: resolved -->\n'+self.block('new',self.new))
        self.assertEqual(len(h.unresolved_questions(self.root)),1)
    def test_fenced_marker_is_not_resolution(self):
        self.put(self.block('new',self.new)+'```\n<!-- handoff-questions: resolved -->\n```\n')
        self.assertEqual(len(h.unresolved_questions(self.root)),1)
    def test_unclosed_fence_blocks_safely(self):
        self.put('## New\n```\n<!-- handoff-questions: resolved -->\n')
        self.assertTrue(h.unanswered(self.root))
    def test_legacy_and_both_resolved(self):
        self.put('Need answer\n<!-- handoff-questions: resolved -->\n')
        self.assertIsNone(h.unanswered(self.root))
        self.put(self.block('old',self.old,True)+self.block('new',self.new,True))
        self.assertIsNone(h.unanswered(self.root))
    def requests(self):
        for agent,path in h.FILES.items():
            p=self.root/path; p.parent.mkdir(parents=True,exist_ok=True)
            p.write_text('## B. Request\n今回の依頼: review '+agent+'\n',encoding='utf-8'); h.publish(p)
    def test_post_run_question_precedes_missing_handoff(self):
        self.requests(); self.put(self.block('old',self.old,True))
        def runner(*args):
            self.put(self.block('old',self.old,True)+self.block('new',self.new))
            return 'ok'
        e=h.Engine(self.root,Path(self.tmp.name)/'runtime',stable=0,runner=runner)
        self.assertEqual(e.once('codex',exe=sys.executable)['status'],'settling')
        result=e.once('codex',exe=sys.executable)
        self.assertEqual(result['status'],'question')
        self.assertEqual(result['questions'][0]['hash'],self.new)
        self.assertTrue(h.load_state(e.runtime)['blocked'].startswith('question:'))
    def test_dryrun_and_once_list_unresolved_sections(self):
        self.requests(); self.put(self.block('new',self.new))
        e=h.Engine(self.root,Path(self.tmp.name)/'runtime',stable=0)
        for dry in (True,False):
            result=e.once('codex',dry=dry,exe=sys.executable)
            self.assertEqual(result['questions'][0]['heading'],'new')
            self.assertEqual(result['questions'][0]['hash'],self.new)

if __name__=='__main__': unittest.main()
