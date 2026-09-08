import html
import json
from pathlib import Path

def render(folder):
    folder = Path(folder)
    s = json.loads((folder/'summary.json').read_text(encoding='utf-8'))
    rows = ''.join(f'<tr><th>{html.escape(k)}</th><td>{html.escape(str(v))}</td></tr>'
                   for k,v in s.items() if not isinstance(v,dict))
    body = '<!doctype html><html lang="ja"><meta charset="utf-8"><title>研究用バックテスト</title>'
    body += '<style>body{font:16px system-ui;max-width:1000px;margin:40px auto;padding:20px}table{border-collapse:collapse}td,th{padding:9px;border-bottom:1px solid #ddd;text-align:left}.warning{background:#fff0cc;padding:20px}</style>'
    body += '<h1>信用買残減少バケット・研究結果</h1><p class="warning">研究専用。合成データの成績は実市場での収益性を示しません。売買シグナルの配信・発注は行っていません。</p>'
    body += f'<table>{rows}</table><h2>年別</h2><pre>{html.escape(json.dumps(s["yearly"],ensure_ascii=False,indent=2))}</pre></html>'
    (folder/'report.html').write_text(body,encoding='utf-8')
