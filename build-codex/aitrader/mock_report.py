"""Read-only, self-contained HTML view of verified mock preparation history."""
from __future__ import annotations

import argparse
from datetime import date
from html import escape
from pathlib import Path

from .notification_plan import _inside, _read
from .runner import RunError, digest, normalize_proposal
from .runner_diagnostics import diagnose_mock_run


def _text(value):
    return escape(str(value), quote=True)


def render_mock_report(home, run_id, execution_day):
    """Return HTML only. Uncertain observations never expose order conditions.

    This is a historical mock preview, never current authorization or proof of
    delivery. Diagnosing twice reduces stale reads but is not a cross-DB snapshot.
    """
    home = Path(home).resolve()
    diagnostic = diagnose_mock_run(home, run_id, execution_day)
    content = ''
    reasons = list(diagnostic['reasons'])
    for candidate in diagnostic['candidates']:
        reasons.extend(candidate['reasons'])
    if diagnostic['classification'] == 'COMPLETE':
        folder = _inside(home, home/'runs'/execution_day.isoformat()/run_id)
        try:
            result_path = _inside(home, folder/'result.json')
            proposals_path = _inside(home, folder/'proposals.json')
            result, raw = _read(result_path), _read(proposals_path)
            second = diagnose_mock_run(home, run_id, execution_day)
            if (second['classification'] != 'COMPLETE' or second != diagnostic
                    or digest(result) != digest(_read(result_path))
                    or digest(raw) != digest(_read(proposals_path))):
                raise RunError('観測中に記録が変更されています')
            proposals = {p.proposal_id: p for p in map(normalize_proposal, raw)}
            approved = [item for item in result['candidates'] if item['status'] == 'APPROVED']
            rows = []
            for item in approved:
                proposal = proposals[item['proposal_id']]
                values = (proposal.code, proposal.side, proposal.qty, proposal.limit_price)
                rows.append('<tr>'+''.join('<td>'+_text(value)+'</td>' for value in values)+'</tr>')
            skipped = len(result['candidates']) - len(approved)
            content = ('<section><h2>準備時点の模擬シグナル</h2>'
                       '<p>承認 '+_text(len(approved))+' 件 ／ 見送り '+_text(skipped)+' 件</p>')
            if rows:
                content += ('<table><thead><tr><th>銘柄コード</th><th>方向</th><th>株数</th>'
                            '<th>指値（円）</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table>')
            else:
                content += '<p>表示できる承認済みシグナルはありません。</p>'
            content += ('<p>準備時点の記録：NOT_SENT。これは現在の送信状況ではありません。</p>'
                        '<p>模擬配信を行った場合、その結果は別の模擬配信記録で確認してください。'
                        '本画面は配信記録を検証せず、実送信・発注の証拠を表示しません。</p></section>')
        except (RunError, ValueError, KeyError, TypeError, AttributeError):
            content = ''
            reasons = ['REPORT_SOURCE_UNVERIFIED']
    if not content:
        reason_list = ''.join('<li>'+_text(reason)+'</li>' for reason in dict.fromkeys(reasons))
        content = ('<section><h2>照合が必要です</h2><p>整合を確認できないためシグナルを表示しません。</p>'
                   '<ul>'+reason_list+'</ul></section>')
    return ('<!doctype html><html lang="ja"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; style-src &#39;unsafe-inline&#39;">'
            '<title>模擬シグナル確認</title><style>'
            'body{font-family:system-ui,sans-serif;background:#f4f6fa;color:#172337;max-width:960px;margin:32px auto;padding:0 20px;line-height:1.7}'
            'header,section{background:white;border:1px solid #d6dfeb;border-radius:12px;padding:24px;margin:18px 0}'
            '.warning{background:#fff1cc;border:3px solid #ac6500;color:#633600;font-size:24px;font-weight:bold;padding:20px}'
            'table{border-collapse:collapse;width:100%}th,td{padding:12px;text-align:left;border-bottom:1px solid #d6dfeb}'
            'footer{font-size:14px;color:#46546b}</style></head><body>'
            '<div class="warning" role="alert">模擬データ・実際の注文には使用禁止</div>'
            '<header><h1>模擬シグナル確認</h1><p>執行日 '+_text(execution_day)
            +' ／ run '+_text(run_id)+'</p><p>実送信なし。この画面に送信・注文機能はありません。</p></header>'
            +content+'<footer>停止中の保存記録の読取です。DB間の同時点整合は保証しません。'
            '表示は自動再開や現在の売買の許可ではありません。</footer></body></html>')


def main(argv=None):
    parser = argparse.ArgumentParser(description='検証済み模擬シグナルの静的HTMLを標準出力')
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--day', type=date.fromisoformat, required=True)
    args = parser.parse_args(argv)
    try:
        html = render_mock_report(args.home, args.run_id, args.day)
    except (RunError, ValueError) as exc:
        parser.exit(2, f'{exc}\n')
    print(html)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
