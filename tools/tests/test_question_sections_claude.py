"""2026-09-09 Claude（Cowork）独立確認: 質問ブロック単位の解決判定（unresolved_questions）への反例。
E01/E02 は現行実装で失敗する（＝欠陥の再現）。skip/条件緩和はしない。"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'automation'))
import codex_engine as h  # noqa: E402

M = '<!-- handoff-questions: resolved -->\n'
H = 'a' * 64


class QuestionSectionsClaude(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'root'
        self.root.mkdir()
        self.q = self.root / h.QUESTIONS[0]
        self.q.parent.mkdir(parents=True)

    def put(self, text):
        self.q.write_text(text, encoding='utf-8')
        return h.unresolved_questions(self.root)

    def test_e01_text_appended_after_marker_in_same_section_is_a_new_question(self):
        """解決マーカーの下に見出しなしで追記された質問は、同じ ## ブロック内でも未解決でなければならない。
        （現行: ブロック内にマーカーが 1 つあればブロック全体を解決済みとみなし、追記が飲み込まれる）"""
        pending = self.put('## 第15回\n依頼hash: `' + H + '`\n保存先を変えてよいか\n' + M + '\n追記: 別の質問。受入テストを保存できない\n')
        self.assertEqual(len(pending), 1, '解決マーカー後の追記が解決済み扱いになっている')
        self.assertEqual(pending[0]['heading'], '第15回')

    def test_e02_legacy_question_before_first_section_is_not_ignored(self):
        """先頭の ## より前にある本文（タイトル 1 行以外）は質問として扱う。
        （現行: 最初の ## より前は『文書タイトル』として無視され、見出しなしの質問が落ちる）"""
        pending = self.put('# 確認事項\n見出しなしの質問: 親フォルダに書けません\n\n## 別件\n解決済み\n' + M)
        self.assertEqual(len(pending), 1, '先頭セクション前の質問が無視されている')
        self.assertEqual(pending[0]['heading'], '(legacy)')

    def test_e03_title_only_prefix_is_not_a_question(self):
        """タイトル行だけの前置きは質問ではない（E02 の裏。現行どおり）。"""
        self.assertEqual(self.put('# 則光さんへの確認事項\n\n## 第15回\n質問\n' + M), [])

    def test_e04_marker_variants_are_not_resolution(self):
        """字下げ・空白なし・波括弧フェンス内・行内のマーカーは解決にならない（安全側。現行どおり）。"""
        for text in ('## a\nQ\n  ' + M, '## a\nQ\n<!--handoff-questions: resolved-->\n', '## a\nQ\n~~~\n' + M + '~~~\n',
                     '## a\nQ\n回答済み <!-- handoff-questions: resolved -->\n'):
            self.assertEqual(len(self.put(text)), 1, text)

    def test_e05_marker_inside_multiline_html_comment_should_not_resolve(self):
        """複数行 HTML コメントの内側に書かれたマーカーは解決にならない（現行: 解決扱い。C 判定）。"""
        pending = self.put('## a\nQ\n<!-- メモ\n' + M + '-->\n')
        self.assertEqual(len(pending), 1)

    def test_e06_duplicate_headings_are_separate_sections(self):
        """同名見出しが 2 回現れても別ブロック。前のブロックの解決は後に及ばない（現行どおり）。"""
        pending = self.put('## a\nQ\n' + M + '## a\nQ2\n')
        self.assertEqual(len(pending), 1)

    def test_e07_two_different_hashes_in_one_section_report_no_hash(self):
        pending = self.put('## a\n依頼hash: `' + 'a' * 64 + '`\n依頼hash: `' + 'b' * 64 + '`\nQ\n')
        self.assertEqual(pending, [{'file': h.QUESTIONS[0], 'heading': 'a', 'hash': None}])


if __name__ == '__main__':
    unittest.main()
