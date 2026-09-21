"""PS5.1 integration in isolated copies. Scheduler mutation cmdlets are mocked."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import codex_engine as h

PS = Path(os.environ.get('SystemRoot', 'C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
SOURCE = Path(__file__).resolve().parents[1]/'automation'


def literal(value):
    return "'"+str(value).replace("'", "''")+"'"


@unittest.skipUnless(os.name == 'nt', 'PS5.1 integration on Windows')
class WindowsEntryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='handoff-ps-review-')
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root = self.base/"日本語 ! & ' project"
        self.scripts = self.root/'tools/automation'
        self.scripts.mkdir(parents=True)
        for file in SOURCE.iterdir():
            if file.suffix in ('.ps1', '.py'):
                shutil.copy2(file, self.scripts/file.name)
        for channel in h.CHANNELS.values():
            for file in channel['files'].values():
                path = self.root/file
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('## A. rules\nsynthetic\n## B. request\n今回の依頼: synthetic\n', encoding='utf-8')
                h.publish(path)
        self.runtime = self.base/"runtime ' &"

    def run_ps(self, *args):
        return subprocess.run([str(PS), '-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',*args],
                              cwd=self.root, capture_output=True, timeout=25)

    def script(self, name, *args):
        return self.run_ps('-File', str(self.scripts/name), *args)

    def encoded(self, body):
        body = '[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)\n'+body
        return self.run_ps('-EncodedCommand', base64.b64encode(body.encode('utf-16le')).decode())

    def registration_args(self):
        return ['-PythonExe',sys.executable,'-CodexExe',sys.executable,'-RuntimeHome',str(self.runtime)]

    def test_register_dryrun_no_files_changed(self):
        before = {p.relative_to(self.base):p.read_bytes() for p in self.base.rglob('*') if p.is_file()}
        result = self.script('register_watch_task.ps1', '-DryRun', *self.registration_args())
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        plan = json.loads(result.stdout.decode('utf-8-sig'))
        self.assertTrue(plan['StartNow'])
        self.assertEqual(plan['WorkingDirectory'], str(self.root))
        self.assertEqual({p.relative_to(self.base):p.read_bytes() for p in self.base.rglob('*') if p.is_file()}, before)

    def test_encoded_task_action_quotes_paths_and_preserves_exit_code(self):
        result = self.script('register_watch_task.ps1', '-DryRun', *self.registration_args())
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        plan = json.loads(result.stdout.decode('utf-8-sig'))
        # Replace only the isolated watch entrypoint with a harmless argument recorder.
        output = self.base/'received.json'
        stub = "param($Agent,$Channel,$MaxRunsPerDay,$PythonExe,$RuntimeHome,$CodexExe)\n" +\
               "$PSBoundParameters | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath "+literal(output)+"\nexit 7\n"
        (self.scripts/'watch_handoff.ps1').write_text(stub, encoding='utf-8-sig')
        encoded = plan['Arguments'].split()[-1]
        ran = self.run_ps('-EncodedCommand', encoded)
        self.assertEqual(ran.returncode, 7, ran.stderr.decode(errors='replace'))
        values = json.loads(output.read_text(encoding='utf-8-sig'))
        self.assertEqual(values['RuntimeHome'], str(self.runtime))
        self.assertEqual(Path(values['CodexExe']), Path(sys.executable))
        self.assertEqual(values['Channel'], 'auto,dev')

    def test_invalid_channel_and_limit_are_rejected(self):
        for args in [('-Channel', "auto';exit 0;#"), ('-MaxRunsPerDay','0')]:
            with self.subTest(args=args):
                self.assertNotEqual(self.script('register_watch_task.ps1', '-DryRun', *args).returncode, 0)
        self.assertFalse(self.runtime.exists())

    def test_register_only_uses_interactive_limited_principal_without_starting(self):
        stubs = r'''
$global:handoffTestCalls=@{}
function Get-ScheduledTask { param($TaskName,$ErrorAction) return $null }
function New-ScheduledTaskAction { param($Execute,$WorkingDirectory,$Argument) $global:handoffTestCalls.action=$PSBoundParameters; return @{} }
function New-ScheduledTaskTrigger { param($AtLogOn,$User) return @{} }
function New-ScheduledTaskPrincipal { param($UserId,$LogonType,$RunLevel) $global:handoffTestCalls.principal=$PSBoundParameters; return @{} }
function New-ScheduledTaskSettingsSet { param($ExecutionTimeLimit,[switch]$StartWhenAvailable,$MultipleInstances,[switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries) $global:handoffTestCalls.settings=$PSBoundParameters; return @{} }
function Register-ScheduledTask { param($TaskName,$Description,$Action,$Trigger,$Settings,$Principal,[switch]$Force) $global:handoffTestCalls.register=$TaskName }
function Start-ScheduledTask { throw 'Unexpected actual start' }
'''.replace('param($AtLogOn,$User)', 'param([switch]$AtLogOn,$User)')
        body = stubs+'\n& '+literal(self.scripts/'register_watch_task.ps1')+' -RegisterOnly '+\
               ' -PythonExe '+literal(sys.executable)+' -CodexExe '+literal(sys.executable)+' -RuntimeHome '+literal(self.runtime)+\
               '\n$global:handoffTestCalls | ConvertTo-Json -Depth 8'
        result = self.encoded(body)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        output = result.stdout.decode('utf-8-sig')
        data = json.loads(output[output.index('{'):])
        self.assertEqual(data['principal']['LogonType'], 'Interactive')
        self.assertEqual(data['principal']['RunLevel'], 'Limited')
        self.assertEqual(data['settings']['MultipleInstances'], 'IgnoreNew')
        self.assertNotIn('RestartCount', data['settings'])
        self.assertFalse(self.runtime.exists())

    def test_running_existing_task_is_not_replaced(self):
        body = "function Get-ScheduledTask { param($TaskName,$ErrorAction) [pscustomobject]@{Description="+literal('ai-trader handoff: '+str(self.root))+";State='Running'} }\n"+\
               "function Register-ScheduledTask { throw 'MUST NOT REGISTER' }\n"+\
               '& '+literal(self.scripts/'register_watch_task.ps1')+' -Replace -PythonExe '+literal(sys.executable)+\
               ' -CodexExe '+literal(sys.executable)+' -RuntimeHome '+literal(self.runtime)
        result = self.encoded(body)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Stop the running task', result.stderr.decode(errors='replace'))

    def test_watch_and_pingpong_and_publish_dryrun_channels(self):
        for name, args, expected in [
            ('watch_handoff.ps1', [], ['auto','dev']),
            ('pingpong.ps1', ['-Channel','dev'], ['dev'])]:
            result = self.script(name, '-DryRun', *args, *self.registration_args())
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
            rows = [json.loads(line) for line in result.stdout.decode('utf-8-sig').splitlines()]
            self.assertEqual([row['channel'] for row in rows], expected)
        before = (self.root/'Codex引き渡しプロンプト.md').read_bytes()
        result = self.script('publish_handoff.ps1','-Channel','dev','-Agent','codex','-DryRun','-PythonExe',sys.executable)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertEqual((self.root/'Codex引き渡しプロンプト.md').read_bytes(), before)
        self.assertFalse(self.runtime.exists())


if __name__ == '__main__':
    unittest.main()
