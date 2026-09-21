"""Static read-only operations view; no actions, remote assets or order fields."""
from __future__ import annotations

import argparse
from html import escape
from pathlib import Path

from .operations_status import build_operations_status


LABELS = {
    'CLEAR': '停止なし・履歴一致', 'STOPPED': '新規処理を停止中',
    'UNKNOWN': '状態を確認できません', 'NEEDS_RECONCILIATION': '履歴の確認が必要',
    'VERIFIED_HISTORY': '保存履歴の一致を確認', 'COMPLETED': '完了の記録あり',
    'FAILED': '失敗の記録あり', 'RUNNING': '処理中の記録あり',
    'CANDIDATES': '模擬候補の記録あり', 'NO_SIGNAL': '模擬候補なし',
    'NO_SESSION': '取引日ではありません', 'DATA_INCOMPLETE': 'データ不足',
    'NOT_APPROVED': '候補を見送り', 'REVIEW_INCOMPLETE': '審査未完了',
    'REVIEW_INVALID': '審査結果を確認できません',
}
STAGES = {'initialize': '初期化', 'acquire_validate': '取得・検査',
          'generate': '候補作成', 'review': '審査', 'prepare': '通知準備',
          'enqueue': 'キュー登録', 'finalize': '終了記録'}


def _text(value):
    return escape(str(value), quote=True)


def render_operations_view(home, *, now):
    """Return a display snapshot, never a current investment authorization."""
    report = build_operations_status(home, now=now)
    stop, history, progress = report['stop'], report['history'], report['progress']
    status = report['status']
    badge = 'good' if status == 'CLEAR' else 'hold'
    stop_label = ('停止なし（売買の許可ではありません）' if stop['status'] == 'CLEAR'
                  else LABELS.get(stop['status'], LABELS['UNKNOWN']))
    summary = history.get('summary') if history['status'] == 'VERIFIED_HISTORY' else None
    historical = ''
    if summary:
        historical = ('<dl><dt>保存対象日</dt><dd>'+_text(summary['execution_day'])+'</dd>'
                      '<dt>保存時の結果</dt><dd>'+_text(LABELS.get(summary['status'], '要確認'))+'</dd>'
                      '<dt>保存時の模擬予約</dt><dd>'+_text(summary.get('reserved', summary['reservation']))
                      +' 円</dd></dl>')
    reason = history.get('reason')
    if reason:
        historical += '<details><summary>照合の情報</summary><p>'+_text(reason)+'</p></details>'
    stage = STAGES.get(progress.get('current_stage'), '確認できません')
    reasons = ''.join('<li>'+_text(reason)+'</li>' for reason in stop.get('reasons', []))
    return ('<!doctype html><html lang="ja"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; style-src &#39;unsafe-inline&#39;; base-uri &#39;none&#39;; form-action &#39;none&#39;">'
            '<title>模擬運用の状態</title><style>'
            'body{margin:0;background:#f3f5f8;color:#182d42;font:16px/1.7 system-ui,sans-serif}'
            'main{max-width:1040px;margin:40px auto;padding:0 24px}h1{font-size:32px;margin:8px 0}'
            '.eyebrow{font-size:13px;letter-spacing:.12em;color:#526378}.notice{padding:16px 20px;background:#fff2d2;border-left:5px solid #ad6b0b}'
            '.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:18px;margin:24px 0}'
            'section{background:white;border:1px solid #d7e0e9;border-radius:12px;padding:24px}h2{font-size:17px;margin:0 0 12px}'
            '.value{font-size:21px;font-weight:700}.badge{display:inline-block;border-radius:8px;padding:8px 14px;margin:12px 0}'
            '.good{background:#dfeef1;color:#124751}.hold{background:#fff0d4;color:#704404}'
            '.muted,footer,dt{color:#556578;font-size:14px}dd{margin:0 0 12px}details{font-size:13px;overflow-wrap:anywhere}'
            '</style></head><body><main><header><div class="eyebrow">AI TRADER / 模擬運用</div>'
            '<h1>運用の状態を確認</h1><p class="muted">観測指定時刻 '+_text(report['observed_at'])+'</p>'
            '<span class="badge '+badge+'">'+_text(LABELS.get(status, LABELS['UNKNOWN']))+'</span></header>'
            '<p class="notice">模擬環境の読取表示です。現在の売買承認ではありません。'
            'この表示から送信・注文・停止解除は行いません。</p><div class="grid">'
            '<section><h2>1. 今回観測した停止状態</h2><p class="value">'+_text(stop_label)+'</p>'
            '<p class="muted">不明な場合、新規処理を進められるとは判断しません。</p>'
            '<details><summary>停止の情報</summary><ul>'+reasons+'</ul></details></section>'
            '<section><h2>2. 保存済みの日次履歴</h2><p class="value">'
            +_text(LABELS.get(history['status'], '履歴の確認が必要'))+'</p>'+historical
            +'<p class="muted">一致は保存時点の履歴確認です。現在の残高・送信実績の保証ではありません。</p></section>'
            '<section><h2>3. 進捗ファイルの記録</h2><p class="value">'
            +_text(LABELS.get(progress['status'], LABELS['UNKNOWN']))+'</p><p>段階：'+_text(stage)+'</p>'
            '<p class="muted">進捗単独では処理完了や稼働継続を証明しません。</p></section></div>'
            '<footer>各情報は同時点の原子的な観測ではありません。表示後に状態が変わる可能性があります。'
            '自動再開・修復・予約解除は行いません。元の実行フォルダへこの表示を保存しないでください。</footer>'
            '</main></body></html>')


def main(argv=None):
    parser = argparse.ArgumentParser(description='模擬運用状態の静的HTMLを標準出力')
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--now', required=True)
    args = parser.parse_args(argv)
    try:
        print(render_operations_view(args.home, now=args.now))
    except (ValueError, TypeError):
        parser.error('nowにはタイムゾーン付きISO日時を指定してください')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
