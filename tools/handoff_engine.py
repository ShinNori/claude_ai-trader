"""Local, bounded handoff runner. Standard library only; no network in dry-run/tests."""
import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

FILES = {'codex': 'tools/automation/Codex引き渡しプロンプト.md',
         'claude': 'tools/automation/Claude引き渡しプロンプト.md'}
QUESTIONS = ('tools/automation/QUESTIONS_codex.md', 'tools/automation/QUESTIONS_claude.md')
READY = re.compile(r'(?m)^<!-- handoff-ready: ([0-9a-f]{64}) -->[ \t]*$')
JST = timezone(timedelta(hours=9))


class ProtocolError(Exception):
    pass


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


@dataclass
class Request:
    path: Path
    text: str
    section: str
    summary: str
    hash: str
    ready: bool
    start: int
    end: int


def read_request(path):
    text = Path(path).read_text(encoding='utf-8-sig').replace('\r\n', '\n')
    starts = list(re.finditer(r'(?m)^## B[.．][^\n]*\n', text))
    if len(starts) != 1:
        raise ProtocolError('B heading must occur exactly once')
    start = starts[0].start()
    # Ignore headings/separators inside code fences. Pending requests under ### are not active B.
    end, fenced = len(text), False
    offset = starts[0].end()
    for line in text[offset:].splitlines(keepends=True):
        if line.strip().startswith('```'):
            fenced = not fenced
        elif not fenced and (re.match(r'^#{1,3} ', line) or re.match(r'^---\s*$', line)):
            end = offset
            break
        offset += len(line)
    if fenced:
        raise ProtocolError('Unclosed fence in B')
    section = text[start:end]
    entries = re.findall(r'(?m)^今回の依頼[:：][ \t]*(\S[^\n]*)$', section)
    if len(entries) != 1:
        raise ProtocolError('B must contain exactly one request line')
    canonical = READY.sub('', section).strip()+'\n'
    hash_value = digest(canonical)
    markers = READY.findall(section)
    return Request(Path(path), text, canonical, entries[0].strip(), hash_value,
                   markers == [hash_value], start, end)


def atomic_text(path, text):
    path = Path(path)
    temp = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def publish(path):
    request = read_request(path)
    # No runtime state changed. Atomic replace publishes the complete B with a digest.
    updated = request.text[:request.start]+request.section+'\n<!-- handoff-ready: '+request.hash+' -->\n\n'+request.text[request.end:]
    if Path(path).read_text(encoding='utf-8-sig').replace('\r\n','\n') != request.text:
        raise ProtocolError('File changed during publication; retry after editing ends')
    atomic_text(path, updated)
    return request.hash


def unanswered(root):
    for name in QUESTIONS:
        path = root/name
        if path.exists():
            body = path.read_text(encoding='utf-8-sig').strip()
            if body and not re.search(r'(?m)^<!-- handoff-questions: resolved -->$', body):
                return name
    return None


def runtime_dir(root, requested=None):
    base = Path(requested or os.environ.get('AI_TRADER_HOME', str(Path.home()/'.ai-trader'))).resolve()
    # No runtime logs/state in the shared checkout or Dropbox tree.
    if base == root or root in base.parents or any('dropbox' in p.lower() for p in base.parts):
        raise ProtocolError('Runtime directory must be outside Dropbox and the checkout')
    return base/'automation-handoff'/digest(str(root).casefold())[:16]


@contextmanager
def lock(runtime):
    runtime.mkdir(parents=True, exist_ok=True)
    path = runtime/'project.lock'
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(fd, json.dumps({'pid': os.getpid(), 'started': datetime.now(JST).isoformat()}).encode())
        yield
    finally:
        os.close(fd)
        path.unlink(missing_ok=True)


def load_state(runtime):
    path = runtime/'state.json'
    if not path.exists():
        return {'processed': {}, 'observations': {}, 'date': '', 'runs': 0, 'blocked': None}
    state = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(state.get('processed'), dict) or not isinstance(state.get('observations'), dict):
        raise ProtocolError('Invalid state; do not reset automatically')
    return state


def save_state(runtime, state):
    atomic_text(runtime/'state.json', json.dumps(state, ensure_ascii=False, indent=2)+'\n')


def executable(agent, explicit=None):
    found = explicit or shutil.which(agent)
    if not found and agent == 'claude':
        candidate = Path.home()/'.local/bin/claude.exe'
        if candidate.is_file():
            found = str(candidate)
    if not found:
        raise ProtocolError(agent+' CLI not found; install/login separately or supply -'+agent.title()+'Exe')
    if Path(found).suffix.lower() in ('.cmd', '.bat', '.ps1'):
        raise ProtocolError('Use a native CLI executable; shell wrappers are not accepted: '+str(found))
    if not Path(found).is_file():
        raise ProtocolError('Executable does not exist: '+str(found))
    return str(Path(found).resolve())


def command(agent, root, last, model='', explicit=None, preview=False):
    exe = (explicit or agent) if preview else executable(agent, explicit)
    if agent == 'codex':
        args = [exe, '-a', 'never', 'exec', '--sandbox', 'workspace-write', '--skip-git-repo-check',
                '-C', str(root), '--output-last-message', str(last)]
    else:
        args = [exe, '-p', '--output-format', 'text', '--permission-mode', 'acceptEdits',
                '--allowedTools', 'Read,Edit,Write,MultiEdit,Glob,Grep,Bash(python:*),Bash(python3:*),Bash(pytest:*)']
    if model:
        args += ['--model', model]
    return args+['-'] if agent == 'codex' else args


def prompt_for(agent, request):
    other = 'claude' if agent == 'codex' else 'codex'
    return f'''ai-trader の作業フォルダで、{FILES[agent]} のAと、以下に固定したBに従って作業してください。
依頼hash: {request.hash}
{request.section}

自動実行規則:
- 自動連携専用の別枠タスクです。ops投資開発の引き渡しMD・未完了依頼は実行/更新しない。
- 編集対象はtoolsの自動連携実装・試験・専用文書だけ。AGENTS.mdのops向けBへ切り替えない。
- 自分宛ての引き渡しMDは読むだけ。履歴追記を含め編集しない。
- 作業中・質問中は相手宛てBを更新・公開しない。質問はtools/automation/QUESTIONS_{agent}.mdへ書いて停止する。
- 完了時のみ {FILES[other]} のBを更新。先頭行は「今回の依頼: …」を1行だけ置く。
- 次の作業がなければ「今回の依頼: 引き渡し不要（理由）」とする。
- 完了したBは python tools/handoff_engine.py publish --agent {other} で公開する。失敗したら公開せず停止。
- 未完了を成功と扱わず、受入試験の削除/skip/xfail/条件緩和をしない。
- 実口座・証券サイト・LINE・実審査LLMを操作しない。これは開発作業のCLI連携だけ。
- QUESTIONS.mdの解決マーカーを自分で追加しない。質問がなければ質問ファイルは更新しない。
- 最終報告は終了時刻(JST)とコピー用の次の一文を表示する。
'''


def run_process(args, prompt, root, folder, timeout):
    folder.mkdir()
    (folder/'prompt.txt').write_text(prompt, encoding='utf-8')
    env = os.environ.copy()
    env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    env['PATH'] = str(Path(sys.executable).parent)+os.pathsep+env.get('PATH','')
    with (folder/'stdout.txt').open('wb') as out, (folder/'stderr.txt').open('wb') as err:
        process = subprocess.Popen(args, cwd=root, stdin=subprocess.PIPE, stdout=out, stderr=err,
                                   env=env, shell=False, creationflags=0x08000000 if os.name=='nt' else 0,
                                   start_new_session=os.name!='nt')
        try:
            process.communicate(prompt.encode('utf-8'), timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            if os.name == 'nt':
                subprocess.run(['taskkill.exe','/PID',str(process.pid),'/T','/F'], capture_output=True,
                               creationflags=0x08000000, timeout=15)
            else:
                import signal
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=15)
            return 'timeout'
    return 'ok' if process.returncode == 0 else 'error'


class Engine:
    def __init__(self, root, runtime=None, stable=60, max_runs=10, timeout=2700, runner=run_process):
        self.root = Path(root).resolve()
        self.runtime = runtime_dir(self.root, runtime)
        self.stable, self.max_runs, self.timeout, self.runner = stable, max_runs, timeout, runner

    def once(self, agent, dry=False, model='', exe=None):
        request = read_request(self.root/FILES[agent])
        if dry:
            state = load_state(self.runtime)
            return {'status':'dryrun', 'agent':agent, 'published':request.ready, 'hash':request.hash,
                    'summary':request.summary, 'question':unanswered(self.root), 'blocked':state['blocked'],
                    'command':command(agent,self.root,self.runtime/'preview_last.md',model,exe,True),
                    'cli_found':bool(exe or shutil.which(agent)), 'runtime':str(self.runtime)}
        try:
            with lock(self.runtime):
                return self._locked(agent, model, exe)
        except FileExistsError:
            return {'status':'locked'}

    def _locked(self, agent, model, exe):
        state = load_state(self.runtime)
        if state['blocked']:
            return {'status':'blocked', 'reason':state['blocked']}
        question = unanswered(self.root)
        if question:
            return {'status':'question', 'file':question}
        request = read_request(self.root/FILES[agent])
        if not request.ready:
            return {'status':'unpublished'}
        key = agent+':'+request.hash
        if key in state['processed']:
            return {'status':'already_processed'}
        if request.summary.startswith('引き渡し不要'):
            return {'status':'done'}
        now = time.time()
        previous = state['observations'].get(agent)
        signature = digest(request.text)  # Detect partial A or B edits, too.
        if previous is None or previous['signature'] != signature:
            state['observations'][agent] = {'signature':signature, 'since':now}
            save_state(self.runtime,state)
            return {'status':'settling'}
        if now-previous['since'] < self.stable or now-request.path.stat().st_mtime < self.stable:
            return {'status':'settling'}
        today = datetime.now(JST).date().isoformat()
        if today != state['date']:
            state['date'], state['runs'] = today, 0
        if state['runs'] >= self.max_runs:
            return {'status':'daily_limit'}
        other = 'claude' if agent=='codex' else 'codex'
        output_before = read_request(self.root/FILES[other]).hash
        folder = self.runtime/(datetime.now(JST).strftime('%Y%m%d_%H%M%S')+'_'+agent+'_'+uuid.uuid4().hex[:8])
        args = command(agent,self.root,folder/'last.md',model,exe)
        # Validate complete input again under the same project lock immediately before claim.
        if read_request(request.path).text != request.text:
            return {'status':'settling'}
        state['processed'][key] = 'running'
        state['runs'] += 1
        state['blocked'] = 'running:'+key  # Crash leaves a durable stop; no automatic retry.
        save_state(self.runtime,state)
        result = 'error'
        try:
            result = self.runner(args,prompt_for(agent,request),self.root,folder,self.timeout)
            if result == 'ok':
                if unanswered(self.root):
                    result = 'question'
                elif read_request(request.path).text != request.text:
                    result = 'self_modified'
                else:
                    next_request = read_request(self.root/FILES[other])
                    if not next_request.ready or next_request.hash == output_before:
                        result = 'nohandoff'
                    else:
                        result = 'done' if next_request.summary.startswith('引き渡し不要') else 'ok'
        except Exception as error:
            result = 'error'
            (self.runtime/'error.txt').write_text(str(error),encoding='utf-8')
        state['processed'][key] = result
        state['blocked'] = None if result in ('ok','done') else result+':'+key
        save_state(self.runtime,state)
        event = {'at':datetime.now(JST).isoformat(),'agent':agent,'hash':request.hash,'summary':request.summary,
                 'status':result,'log':str(folder)}
        with (self.runtime/'history.jsonl').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(event,ensure_ascii=False)+'\n')
        return event


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['watch','pingpong','publish','status','resume'])
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--agent',choices=['codex','claude'],default='codex')
    parser.add_argument('--runtime')
    parser.add_argument('--stable',type=int,default=60)
    parser.add_argument('--interval',type=int,default=60)
    parser.add_argument('--max-runs',type=int,default=10)
    parser.add_argument('--rounds',type=int,default=3)
    parser.add_argument('--timeout',type=int,default=2700)
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--codex-exe'); parser.add_argument('--claude-exe')
    parser.add_argument('--codex-model',default=''); parser.add_argument('--claude-model',default='')
    args = parser.parse_args(argv)
    if min(args.interval,args.max_runs,args.rounds,args.timeout) < 1 or args.stable < 0:
        parser.error('Limits must be positive; stable may be zero')
    engine = Engine(args.root,args.runtime,args.stable,args.max_runs,args.timeout)
    if args.mode == 'publish':
        if args.dry_run:
            print(read_request(engine.root/FILES[args.agent]).hash)
        else:
            print(publish(engine.root/FILES[args.agent]))
        return 0
    if args.mode == 'status':
        print(json.dumps(load_state(engine.runtime),ensure_ascii=False,indent=2))
        return 0
    if args.mode == 'resume':
        if unanswered(engine.root):
            raise ProtocolError('Resolve QUESTIONS.md before resume')
        with lock(engine.runtime):
            state = load_state(engine.runtime)
            state['blocked'] = None
            save_state(engine.runtime,state)  # Keep processed history; publish a revised B to retry.
        return 0
    agent, count, last = args.agent, 0, None
    while True:
        result = engine.once(agent,args.dry_run,getattr(args,agent+'_model'),getattr(args,agent+'_exe'))
        encoded = json.dumps(result,ensure_ascii=False)
        if encoded != last:
            print(encoded,flush=True); last = encoded
        status = result['status']
        if args.once or args.dry_run:
            return 1 if status in ('error','timeout','blocked','question','nohandoff','self_modified') else 0
        if args.mode == 'pingpong':
            if status in ('ok','done'):
                count += 1
                if status == 'done' or count >= args.rounds*2:
                    return 0
                agent = 'claude' if agent=='codex' else 'codex'
            elif status != 'settling':
                return 1
        elif status in ('error','timeout','blocked','question','nohandoff','self_modified','done','daily_limit'):
            return 0 if status=='done' else 1
        time.sleep(args.interval)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ProtocolError, OSError, ValueError) as error:
        print(json.dumps({'status':'error','reason':str(error)},ensure_ascii=False),file=sys.stderr)
        raise SystemExit(1)
