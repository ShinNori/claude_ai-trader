"""2026-09-09 Claude（Cowork/PC）独立確認: PS5.1 の既定コンソール符号化（日本語 Windows の CP932）での反例。

E08 は現行実装で失敗する（＝欠陥の再現）。E09 は現行どおり通過し、修正で壊さないための番人。
子 PowerShell の [Console]::OutputEncoding を CP932 に固定して再現するため、
実行 PC の chcp（65001 でも 932 でも）に依らず同じ結果になる。skip/条件緩和はしない。
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'automation'))
import codex_engine as h  # noqa: E402

PS = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
SOURCE = Path(__file__).resolve().parents[1] / 'automation'
SUMMARY = '質問ブロック判定の反例 E01（A）・E02（B）・E05（C）の修正と回帰試験'


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(os.name == 'nt', 'PS5.1 integration on Windows')
class OemConsoleEncoding(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='handoff-oem-')
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root = self.base / '日本語 project'
        self.scripts = self.root / 'tools/automation'
        self.scripts.mkdir(parents=True)
        for file in SOURCE.iterdir():
            if file.suffix in ('.ps1', '.py'):
                shutil.copy2(file, self.scripts / file.name)
        for channel in h.CHANNELS.values():
            for name in channel['files'].values():
                path = self.root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('## A. rules\nsynthetic\n## B. request\n今回の依頼: ' + SUMMARY + '\n', encoding='utf-8')
                h.publish(path)
        self.runtime = self.base / 'runtime'

    def run_oem(self, name, *args):
        """OEM（CP932）コンソールの子プロセスとして ps1 を動かす。"""
        # パラメーター名はそのまま、値だけ単一引用符で括る（名前を括ると位置引数になる）
        rendered = ' '.join(a if a.startswith('-') else literal(a) for a in args)
        body = ('[Console]::OutputEncoding=[Text.Encoding]::GetEncoding(932)\n& ' + literal(self.scripts / name) + ' '
                + rendered)
        return subprocess.run([str(PS), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                               '-EncodedCommand', base64.b64encode(body.encode('utf-16le')).decode()],
                              cwd=self.root, capture_output=True, timeout=60)

    def registration_args(self):
        return ['-PythonExe', sys.executable, '-CodexExe', sys.executable, '-RuntimeHome', str(self.runtime)]

    def test_e08_register_dryrun_survives_oem_console_codepage(self):
        """CP932 コンソールでも登録 DryRun は成功し、計画 JSON を読めなければならない。
        （現行: engine の UTF-8 JSON を `@(& $PythonExe …)` で捕捉する際に CP932 として復号するため、
        依頼要約が文字化けし、記号の並びによっては ConvertFrom-Json 自体が失敗して登録手順を実行できない）"""
        result = self.run_oem('register_watch_task.ps1', '-DryRun', *self.registration_args())
        self.assertEqual(result.returncode, 0, result.stderr.decode('cp932', errors='replace'))
        plan = json.loads(result.stdout.decode('utf-8-sig'))
        self.assertEqual(plan['WorkingDirectory'], str(self.root))
        self.assertEqual(plan['Preview'][0]['summary'], SUMMARY)

    def test_e09_watch_dryrun_keeps_japanese_summary_readable(self):
        """CP932 コンソールでも見張りの JSON 行（ログに残る診断）が文字化けしてはならない。
        現行は engine の出力を捕捉せず素通しするため通過する。E08 の修正で捕捉方式へ変えないこと。"""
        result = self.run_oem('watch_handoff.ps1', '-Agent', 'codex', '-Channel', 'auto', '-DryRun',
                              *self.registration_args())
        self.assertEqual(result.returncode, 0, result.stderr.decode('cp932', errors='replace'))
        rows = [json.loads(line) for line in result.stdout.decode('utf-8-sig').splitlines() if line.strip()]
        self.assertEqual([row['summary'] for row in rows], [SUMMARY])
        self.assertEqual(rows[0]['command'][rows[0]['command'].index('-C') + 1], str(self.root))


if __name__ == '__main__':
    unittest.main()
