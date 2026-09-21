"""Local, bounded handoff runner. Standard library only; no network in dry-run/tests."""
import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid

# チャネル: auto = 自動連携（このスクリプト群）の作業、dev = 投資システム本体の開発ループ（2026-09-09 Claude 追加）
CHANNELS = {
    'auto': {'files': {'codex': 'tools/automation/Codex引き渡しプロンプト.md',
                       'claude': 'tools/automation/Claude引き渡しプロンプト.md'},
             'questions': ('tools/automation/QUESTIONS_codex.md', 'tools/automation/QUESTIONS_claude.md')},
    'dev':  {'files': {'codex': 'Codex引き渡しプロンプト.md',
                       'claude': 'build-codex/Claude引き渡しプロンプト.md'},
             'questions': ('build-codex/QUESTIONS.md', 'ops/QUESTIONS.md')},
}
FILES = CHANNELS['auto']['files']            # 互換用（auto チャネル）
QUESTIONS = CHANNELS['auto']['questions']
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


def markdown_lines(text):
    """Fenced examples cannot act as B headings or publication markers."""
    offset, fence = 0, None
    for line in text.splitlines(keepends=True):
        token = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line.rstrip('\n'))
        outside = fence is None
        if fence:
            if token and token[1][0] == fence[0] and len(token[1]) >= fence[1] and not token[2].strip():
                fence = None
        elif token:
            fence = (token[1][0], len(token[1]))
        yield offset, line, outside and token is None
        offset += len(line)
    if fence:
        raise ProtocolError('Unclosed Markdown fence')


def read_request(path):
    text = Path(path).read_text(encoding='utf-8-sig').replace('\r\n', '\n')
    lines = list(markdown_lines(text))
    starts = [offset for offset, line, structural in lines if structural and re.match(r'^## B[.．]', line)]
    if len(starts) != 1:
        raise ProtocolError('B heading must occur exactly once')
    start, end = starts[0], len(text)
    for offset, line, structural in lines:
        if offset <= start:
            continue
        if structural and (re.match(r'^#{1,6} ', line) or re.match(r'^---\s*$', line)):
            end = offset
            break
    section = text[start:end]
    entries = re.findall(r'(?m)^今回の依頼[:：][ \t]*(\S[^\n]*)$', section)
    if len(entries) != 1:
        raise ProtocolError('B must contain exactly one request line')
    markers, canonical_lines = [], []
    for offset, line, structural in lines:
        if start <= offset < end:
            marker = READY.fullmatch(line.rstrip('\n')) if structural else None
            if marker:
                markers.append(marker[1])
            else:
                canonical_lines.append(line)
    canonical = ''.join(canonical_lines).strip()+'\n'
    hash_value = digest(canonical)
    return Request(Path(path), text, canonical, entries[0].strip(), hash_value,
                   markers == [hash_value], start, end)


def atomic_text(path, text, *, replace_retries=0):
    path = Path(path)
    temp = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(replace_retries + 1):
            try:
                os.replace(temp, path)
                break
            except OSError as error:
                # Windows readers can temporarily deny replacement. Only the
                # observational status file opts in; protocol writes stay strict.
                if attempt >= replace_retries or getattr(error, 'winerror', None) not in (5, 32, 33):
                    raise
                time.sleep(0.05 * 2**attempt)
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


def unresolved_questions(root, questions=QUESTIONS):
    """Resolve individual question sections, never an entire file by one marker.

    Unresolved older hashes also block: changing a request is not an answer.
    Headerless legacy questions are one section. Fenced markers are examples.
    """
    pending = []
    for name in questions:
        path = Path(root)/name
        if not path.exists():
            continue
        body = path.read_text(encoding='utf-8-sig').replace('\r\n', '\n')
        try:
            lines = list(markdown_lines(body))
        except ProtocolError:
            pending.append({'file':name, 'heading':'Malformed question document', 'hash':None})
            continue
        starts = [(offset, line.strip()[3:].strip()) for offset, line, structural in lines
                  if structural and re.match(r'^##[ \t]+', line)]
        # Preserve headerless questions before the first section, excluding titles.
        blocks = ([(0, '(legacy)')] if not starts or starts[0][0] > 0 else []) + starts
        for i, (start, heading) in enumerate(blocks):
            end = blocks[i+1][0] if i+1 < len(blocks) else len(body)
            block = [(line, structural) for offset, line, structural in lines if start <= offset < end]
            visible = ''.join(line for line, structural in block if structural)
            # Only a standalone marker outside a multiline HTML comment resolves
            # preceding text. Later text remains a new unanswered question.
            remaining = []
            in_comment = False
            for line, structural in block:
                if not structural:
                    continue
                if not in_comment and re.fullmatch(r'<!-- handoff-questions: resolved -->[ \t]*\n?', line):
                    remaining = []
                    continue
                if not re.match(r'^#{1,2}[ \t]+', line):
                    remaining.append(line)
                for token in re.findall(r'<!--|-->', line):
                    if token == '<!--':
                        in_comment = True
                    elif in_comment:
                        in_comment = False
            if not ''.join(remaining).strip():
                continue
            hashes = re.findall(r'(?:依頼hash|request[_ -]?hash)\s*[:：]\s*`?([0-9a-f]{64})', visible, re.I)
            pending.append({'file':name, 'heading':heading, 'hash':hashes[0] if len(set(hashes)) == 1 else None})
    return pending


def unanswered(root, questions=QUESTIONS):
    pending = unresolved_questions(root, questions)
    return pending[0]['file'] if pending else None


STATUS_FILE = 'tools/automation/watch_status.json'


def write_status(root, payload):
    """見張りの生存と直近結果の要約だけを共有フォルダに書く（Cowork 側が PC の runtime を読めないため。ログ・状態本体は runtime に置く）。"""
    payload = dict(payload, written_at=datetime.now(JST).isoformat(), pid=os.getpid())
    filename = 'tools/automation/watch_status_claude.json' if payload.get('agent') == 'claude' else STATUS_FILE
    atomic_text(Path(root)/filename, json.dumps(payload, ensure_ascii=False, indent=2)+'\n', replace_retries=5)


@contextmanager
def status_heartbeat(root, payload, interval):
    """Keep observation fresh while a CLI holds the project lock for a long turn."""
    stop, errors = threading.Event(), []
    write_status(root, dict(payload, phase='checking_or_running'))
    def refresh():
        while not stop.wait(interval):
            try:
                write_status(root, dict(payload, phase='checking_or_running'))
            except Exception as error:
                errors.append(error)
                return
    thread = threading.Thread(target=refresh, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()
    if errors:
        raise errors[0]


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
    if not isinstance(state, dict) or not isinstance(state.get('processed'), dict) or not isinstance(state.get('observations'), dict):
        raise ProtocolError('Invalid state; do not reset automatically')
    if (type(state.get('runs')) is not int or state['runs'] < 0 or not isinstance(state.get('date'), str)
            or 'blocked' not in state or (state['blocked'] is not None and not isinstance(state['blocked'], str))):
        raise ProtocolError('Invalid state counters/block; do not reset automatically')
    if state['date']:
        datetime.strptime(state['date'], '%Y-%m-%d')
    if any(not isinstance(k, str) or not isinstance(v, str) for k, v in state['processed'].items()):
        raise ProtocolError('Invalid processed history')
    for value in state['observations'].values():
        if (not isinstance(value, dict) or not isinstance(value.get('signature'), str)
                or type(value.get('since')) not in (int, float) or not math.isfinite(value['since'])):
            raise ProtocolError('Invalid observations')
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


def prompt_for(agent, request, channel='auto'):
    other = 'claude' if agent == 'codex' else 'codex'
    files = CHANNELS[channel]['files']
    if channel == 'dev':
        return f'''ai-trader の作業フォルダで、{files[agent]} を読み、「A. 固定プロンプト」と以下に固定した「B. 今回の依頼」に従い、{request.summary} を行ってください。
依頼hash: {request.hash}
{request.section}

自動実行規則（開発ループ dev チャネル）:
- これは tools/automation/codex_engine.py による無人実行です。人間はこのターン中に応答できません。
- 自分宛ての引き渡しMD（{files[agent]}）は読むだけ。編集しない。
- 人間の判断が必要なら {CHANNELS[channel]['questions'][0 if agent == 'codex' else 1]} に質問を書いて停止し、相手宛てBを更新・公開しない。
- QUESTIONS.mdの解決マーカーを自分で追加しない。質問がなければ質問ファイルは変更しない。
- 作業・検証・成果物・履歴の保存をすべて終えた最後の操作として、{files[other]} の「B. 今回の依頼」を更新する。先頭行は「今回の依頼: …」を1物理行だけ。
- 次の作業がなければ「今回の依頼: 引き渡し不要（理由）」とする。
- 更新したBは python tools/automation/codex_engine.py publish --channel dev --agent {other} で公開する。失敗したら公開せず停止。
- 自動連携（tools/automation/、tools/自動連携_*.md）の作業はこの開発ループに混ぜない。
- 未完了を成功と扱わず、受入試験の削除/skip/xfail/条件緩和、勝てるまでのパラメータ探索をしない。
- 実口座・証券サイト・LINE・実審査LLMを操作しない。
- 最終報告は「報告の末尾」テンプレート（終了時刻(JST)とコピー用の次の一文）に従う。
'''
    return f'''ai-trader の作業フォルダで、{files[agent]} のAと、以下に固定したBに従って作業してください。
依頼hash: {request.hash}
{request.section}

自動実行規則:
- 自動連携専用の別枠タスクです。ops投資開発の引き渡しMD・未完了依頼は実行/更新しない。
- 編集対象はtools/automation/とtools/tests/だけ。AGENTS.mdのops向けBへ切り替えない。
- 自分宛ての引き渡しMDは読むだけ。履歴追記を含め編集しない。
- 作業中・質問中は相手宛てBを更新・公開しない。質問はtools/automation/QUESTIONS_{agent}.mdへ書いて停止する。
- 完了時のみ {FILES[other]} のBを更新。先頭行は「今回の依頼: …」を1行だけ置く。
- 次の作業がなければ「今回の依頼: 引き渡し不要（理由）」とする。
- 完了したBは python tools/automation/codex_engine.py publish --agent {other} で公開する。失敗したら公開せず停止。
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
    def __init__(self, root, runtime=None, stable=60, max_runs=10, timeout=2700, runner=run_process, channel='auto'):
        self.root = Path(root).resolve()
        self.runtime = runtime_dir(self.root, runtime)
        self.stable, self.max_runs, self.timeout, self.runner = stable, max_runs, timeout, runner
        if channel not in CHANNELS:
            raise ProtocolError('Unknown channel: '+str(channel))
        self.channel = channel
        self.files = CHANNELS[channel]['files']
        self.questions = CHANNELS[channel]['questions']
        self.prefix = '' if channel == 'auto' else channel+':'   # auto は従来の state キーのまま

    def once(self, agent, dry=False, model='', exe=None):
        request = read_request(self.root/self.files[agent])
        if dry:
            state = load_state(self.runtime)
            try:
                resolved, cli_error = executable(agent, exe), None
            except ProtocolError as error:
                resolved, cli_error = None, str(error)
            return {'status':'dryrun', 'channel':self.channel, 'agent':agent, 'published':request.ready, 'hash':request.hash,
                    'summary':request.summary, 'question':unanswered(self.root, self.questions),
                    'questions':unresolved_questions(self.root, self.questions), 'blocked':state['blocked'],
                    'command':command(agent,self.root,self.runtime/'preview_last.md',model,resolved or exe,True),
                    'cli_found':resolved is not None, 'cli_error':cli_error,
                    'processed':self.prefix+agent+':'+request.hash in state['processed'], 'runtime':str(self.runtime)}
        try:
            with lock(self.runtime):
                return self._locked(agent, model, exe)
        except FileExistsError:
            return {'status':'locked'}

    def _locked(self, agent, model, exe):
        state = load_state(self.runtime)
        if state['blocked']:
            return {'status':'blocked', 'reason':state['blocked']}
        question = unanswered(self.root, self.questions)
        if question:
            return {'status':'question', 'file':question, 'questions':unresolved_questions(self.root, self.questions)}
        request = read_request(self.root/self.files[agent])
        if not request.ready:
            return {'status':'unpublished'}
        key = self.prefix+agent+':'+request.hash
        if key in state['processed']:
            return {'status':'already_processed'}
        if request.summary.startswith('引き渡し不要'):
            return {'status':'done'}
        now = time.time()
        previous = state['observations'].get(self.prefix+agent)
        signature = digest(request.text)  # Detect partial A or B edits, too.
        if previous is None or previous['signature'] != signature:
            state['observations'][self.prefix+agent] = {'signature':signature, 'since':now}
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
        output_before = read_request(self.root/self.files[other]).hash
        folder = self.runtime/(datetime.now(JST).strftime('%Y%m%d_%H%M%S')+'_'+self.channel+'_'+agent+'_'+uuid.uuid4().hex[:8])
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
            result = self.runner(args,prompt_for(agent,request,self.channel),self.root,folder,self.timeout)
            if result == 'ok':
                if unanswered(self.root, self.questions):
                    result = 'question'
                elif read_request(request.path).text != request.text:
                    result = 'self_modified'
                else:
                    next_request = read_request(self.root/self.files[other])
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
        event = {'at':datetime.now(JST).isoformat(),'channel':self.channel,'agent':agent,'hash':request.hash,'summary':request.summary,
                 'status':result,'log':str(folder), 'questions':unresolved_questions(self.root, self.questions)}
        with (self.runtime/'history.jsonl').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(event,ensure_ascii=False)+'\n')
        return event


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['watch','pingpong','publish','status','resume'])
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[2])
    parser.add_argument('--agent',choices=['codex','claude'],default='codex')
    parser.add_argument('--channel',default='auto',help='auto | dev | auto,dev（watch のみ複数可）')
    parser.add_argument('--runtime')
    parser.add_argument('--stable',type=int,default=60)
    parser.add_argument('--interval',type=int,default=60)
    parser.add_argument('--max-runs',type=int,default=10)
    parser.add_argument('--rounds',type=int,default=3)
    parser.add_argument('--timeout',type=int,default=2700)
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--no-status-file',action='store_true',help='tools/automation/watch_status.json を書かない')
    parser.add_argument('--codex-exe'); parser.add_argument('--claude-exe')
    parser.add_argument('--codex-model',default=''); parser.add_argument('--claude-model',default='')
    args = parser.parse_args(argv)
    if min(args.interval,args.max_runs,args.rounds,args.timeout) < 1 or args.stable < 0:
        parser.error('Limits must be positive; stable may be zero')
    channels = [c.strip() for c in args.channel.split(',') if c.strip()]
    if not channels or len(set(channels)) != len(channels) or any(c not in CHANNELS for c in channels):
        parser.error('--channel は auto / dev / auto,dev')
    if len(channels) > 1 and args.mode != 'watch':
        parser.error('複数チャネルは watch のみ')
    engine = Engine(args.root,args.runtime,args.stable,args.max_runs,args.timeout,channel=channels[0])
    engines = [engine]+[Engine(args.root,args.runtime,args.stable,args.max_runs,args.timeout,channel=c) for c in channels[1:]]
    if args.mode == 'publish':
        if args.dry_run:
            print(read_request(engine.root/engine.files[args.agent]).hash)
        else:
            print(publish(engine.root/engine.files[args.agent]))
        return 0
    if args.mode == 'status':
        print(json.dumps(load_state(engine.runtime),ensure_ascii=False,indent=2))
        return 0
    if args.mode == 'resume':
        if args.dry_run:
            print(json.dumps({'status':'resume_preview', 'blocked':load_state(engine.runtime)['blocked'],
                  'questions':[q for ch in CHANNELS.values() if (q := unanswered(engine.root, ch['questions']))]},ensure_ascii=False))
            return 0
        with lock(engine.runtime):
            if any(unanswered(engine.root, ch['questions']) for ch in CHANNELS.values()):
                raise ProtocolError('Resolve QUESTIONS.md in all channels before resume')
            state = load_state(engine.runtime)
            state['blocked'] = None
            save_state(engine.runtime,state)  # Keep processed history; publish a revised B to retry.
        return 0
    agent, count, last, seen = args.agent, 0, {}, {}
    while True:
        # watch で複数チャネルのときは順に 1 回ずつ確認し、最初に起動したチャネルの結果で判定する
        result, tick_statuses = None, []
        for e in engines:
            if args.mode == 'watch' and not args.dry_run and not args.no_status_file:
                payload = {'mode':'watch','agent':agent,'channels':dict(seen),'active_channel':e.channel,
                           'interval':args.interval}
                with status_heartbeat(e.root, payload, args.interval):
                    result = e.once(agent,False,getattr(args,agent+'_model'),getattr(args,agent+'_exe'))
            else:
                result = e.once(agent,args.dry_run,getattr(args,agent+'_model'),getattr(args,agent+'_exe'))
            tick_statuses.append(result['status'])
            encoded = json.dumps(result,ensure_ascii=False)
            if encoded != last.get(e.channel):
                print(encoded,flush=True); last[e.channel] = encoded
            seen[e.channel] = {'status':result['status'], 'at':datetime.now(JST).isoformat()}
            if result.get('log'):
                break  # An executed done result also consumes this tick's single-run allowance.
            if result['status'] not in ('settling','unpublished','already_processed','done','dryrun','daily_limit','question'):
                break
        status = result['status']
        if args.mode == 'watch' and not args.dry_run and not args.no_status_file:
            try:
                blocked = load_state(engine.runtime).get('blocked')
            except ProtocolError:
                blocked = 'state-invalid'
            write_status(engine.root, {'mode':'watch','agent':agent,'channels':seen,'blocked':'blocked' if blocked else None,
                                       'interval':args.interval,'phase':'observed'})
        if args.once or args.dry_run:
            return 1 if any(s in ('error','timeout','blocked','question','nohandoff','self_modified') for s in tick_statuses) else 0
        if args.mode == 'pingpong':
            if status in ('ok','done'):
                count += 1
                if status == 'done' or count >= args.rounds*2:
                    return 0
                agent = 'claude' if agent=='codex' else 'codex'
            elif status != 'settling':
                return 1
        elif status in ('error','timeout','blocked','nohandoff','self_modified'):
            return 1
        elif status == 'question' and len(engines) == 1:
            return 1                              # 複数チャネルの常駐では、片方の質問待ちは他チャネルの見張りを止めない（質問はチャネル別）
        elif status in ('done','daily_limit') and len(engines) == 1:
            return 0 if status=='done' else 1     # 単一チャネルは従来どおり終了。複数チャネルの常駐は他チャネルのため待機を続ける
        time.sleep(args.interval)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ProtocolError, OSError, ValueError) as error:
        print(json.dumps({'status':'error','reason':str(error)},ensure_ascii=False),file=sys.stderr)
        raise SystemExit(1)
