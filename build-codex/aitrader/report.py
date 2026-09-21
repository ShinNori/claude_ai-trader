import html
import json
import os
import stat
import tempfile
from pathlib import Path

_MAX_SUMMARY_BYTES = 8 * 1024 * 1024


def _guard(folder):
    targets = [(part, True) for part in reversed((folder, *folder.parents))]
    targets += [(folder/name, False) for name in ('summary.json', 'report.html')]
    for target, directory in targets:
        try:
            info = target.lstat()
        except FileNotFoundError:
            if directory or target.name == 'summary.json':
                raise ValueError('レポート入力がありません') from None
            continue
        unsafe = (stat.S_ISLNK(info.st_mode)
                  or bool(getattr(info, 'st_file_attributes', 0) & 0x400)
                  or (hasattr(os.path, 'isjunction') and os.path.isjunction(target)))
        valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if unsafe or not valid:
            raise ValueError('レポートの保存先を安全に確認できません')


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _read_summary(folder):
    _guard(folder)
    path = folder/'summary.json'
    before = path.lstat()
    if before.st_size > _MAX_SUMMARY_BYTES:
        raise ValueError('レポート入力が大きすぎます')
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or _identity(before) != _identity(opened):
            raise ValueError('レポート入力が観測中に変化しました')
        value = stream.read(_MAX_SUMMARY_BYTES + 1)
    _guard(folder)
    if (_identity(before) != _identity(path.lstat())
            or len(value) != before.st_size or len(value) > _MAX_SUMMARY_BYTES):
        raise ValueError('レポート入力が観測中に変化しました')
    return value


def _html(summary_bytes):
    try:
        s = json.loads(summary_bytes.decode('utf-8'))
        if not isinstance(s, dict) or not isinstance(s.get('yearly'), dict):
            raise ValueError()
        json.dumps(s, allow_nan=False)
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise ValueError('レポート入力の形式を確認できません') from None
    rows = ''.join(f'<tr><th>{html.escape(k)}</th><td>{html.escape(str(v))}</td></tr>'
                   for k,v in s.items() if not isinstance(v,dict))
    body = '<!doctype html><html lang="ja"><meta charset="utf-8"><title>研究用バックテスト</title>'
    body += '<style>body{font:16px system-ui;max-width:1000px;margin:40px auto;padding:20px}table{border-collapse:collapse}td,th{padding:9px;border-bottom:1px solid #ddd;text-align:left}.warning{background:#fff0cc;padding:20px}</style>'
    body += '<h1>信用買残減少バケット・研究結果</h1><p class="warning">研究専用。合成データの成績は実市場での収益性を示しません。売買シグナルの配信・発注は行っていません。</p>'
    body += f'<table>{rows}</table><h2>年別</h2><pre>{html.escape(json.dumps(s["yearly"],ensure_ascii=False,indent=2))}</pre></html>'
    return body


def render(folder):
    """Regenerate HTML under the result writer lock; this does not verify CSVs."""
    folder = Path(folder).absolute()
    _guard(folder)
    lock = folder.parent/('.'+folder.name+'.publish.lock')
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError('レポートの公開処理が既に実行中または要確認です') from None
    owner = os.fstat(descriptor)
    os.close(descriptor)
    temporary = None
    try:
        original = _read_summary(folder)
        body = _html(original)
        descriptor, filename = tempfile.mkstemp(prefix='.report.', suffix='.tmp', dir=folder)
        temporary = Path(filename)
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as stream:
            stream.write(body)
        if _read_summary(folder) != original:
            raise ValueError('レポート入力が生成中に変化しました')
        _guard(folder)
        os.replace(temporary, folder/'report.html')
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        try:
            current = lock.lstat()
        except FileNotFoundError:
            current = None
        if current is not None and _identity(current) == _identity(owner):
            lock.unlink()
