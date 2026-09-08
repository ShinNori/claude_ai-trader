"""results/<strategy>_<version>/ から report.html を生成（外部ライブラリ不要・単一HTML）"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def _svg_line(xs: list[float], series: list[tuple[str, list[float], str]], w=880, h=300) -> str:
    allv = [v for _, ys, _ in series for v in ys if v == v]
    lo, hi = min(allv), max(allv)
    if hi == lo:
        hi = lo + 1
    n = len(xs)
    def pt(i, v):
        return f"{40 + (w - 60) * i / max(n - 1, 1):.1f},{h - 30 - (h - 60) * (v - lo) / (hi - lo):.1f}"
    paths = []
    for name, ys, color in series:
        pts = " ".join(pt(i, v) for i, v in enumerate(ys) if v == v)
        paths.append(f'<polyline fill="none" stroke="{color}" stroke-width="1.5" points="{pts}"/>'
                     f'<text x="{w-150}" y="{20 + 16*len(paths)}" fill="{color}" font-size="12">{name}</text>')
    grid = "".join(
        f'<line x1="40" x2="{w-20}" y1="{h-30-(h-60)*k/4:.1f}" y2="{h-30-(h-60)*k/4:.1f}" stroke="#ddd"/>'
        f'<text x="2" y="{h-26-(h-60)*k/4:.1f}" font-size="10" fill="#666">{lo+(hi-lo)*k/4:,.0f}</text>'
        for k in range(5))
    return f'<svg viewBox="0 0 {w} {h}" width="100%" style="max-width:{w}px">{grid}{"".join(paths)}</svg>'


def build_report(results_dir: Path) -> Path:
    results_dir = Path(results_dir)
    s = json.loads((results_dir / "summary.json").read_text(encoding="utf-8"))
    eq = pd.read_csv(results_dir / "equity.csv")
    tr = pd.read_csv(results_dir / "trades.csv") if (results_dir / "trades.csv").exists() else pd.DataFrame()

    bench = pd.to_numeric(eq["benchmark"], errors="coerce").tolist()
    chart = _svg_line(list(range(len(eq))), [("戦略", eq["equity"].tolist(), "#1f5fbf"), ("TOPIX", bench, "#999")])
    dd = (eq["equity"] / eq["equity"].cummax() - 1)
    dd_chart = _svg_line(list(range(len(eq))), [("ドローダウン", dd.tolist(), "#c0392b")], h=160)

    def pct(v):
        return "-" if v is None else f"{v*100:.1f}%"
    kpis = [
        ("最終資産", f"{s['final_equity']:,.0f} 円"), ("年率(CAGR)", pct(s["cagr"])), ("シャープ", f"{s['sharpe']:.2f}"),
        ("最大DD", pct(s["max_drawdown"])), ("取引回数", s["trades"]), ("勝率", pct(s["win_rate"])),
        ("PF", "-" if s["profit_factor"] is None else f"{s['profit_factor']:.2f}"),
        ("指値未約定", s["skipped_by_limit"]), ("TOPIX CAGR", pct(s["benchmark_cagr"])),
        ("TOPIX 最大DD", pct(s["benchmark_max_drawdown"])), ("TOPIX相関", f"{s['corr_to_benchmark']:.2f}"),
        ("データ", s["data_mode"]),
    ]
    kpi_html = "".join(f'<div class="k"><div class="l">{k}</div><div class="v">{v}</div></div>' for k, v in kpis)
    yearly = "".join(f"<tr><td>{y}</td><td>{pct(v['return'])}</td><td>{v['trades']}</td></tr>"
                     for y, v in s["yearly"].items())
    recent = tr.tail(30).to_html(index=False, border=0) if len(tr) else "<p>取引なし</p>"

    html = f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<title>{s['strategy']} {s['version']} バックテスト</title>
<style>body{{font-family:system-ui,-apple-system,"Segoe UI",Meiryo,sans-serif;margin:24px;color:#222}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px;margin:16px 0}}
.k{{border:1px solid #e3e3e3;border-radius:8px;padding:10px}} .l{{font-size:12px;color:#666}} .v{{font-size:18px;font-weight:600}}
table{{border-collapse:collapse;font-size:13px}} td,th{{padding:4px 10px;border-bottom:1px solid #eee;text-align:right}} th{{background:#f6f6f6}}
h2{{margin-top:32px;font-size:16px}} .note{{font-size:12px;color:#777}}</style></head><body>
<h1>{s['strategy']} {s['version']} — バックテスト結果</h1>
<p class="note">期間 {s['period']['from']} 〜 {s['period']['to']} / 初期資金 {s['initial_capital']:,.0f} 円 / 生成 {s['generated_at']}</p>
<div class="kpis">{kpi_html}</div>
<h2>資産推移</h2>{chart}
<h2>ドローダウン</h2>{dd_chart}
<h2>年別</h2><table><tr><th>年</th><th>リターン</th><th>取引</th></tr>{yearly}</table>
<h2>直近30取引</h2>{recent}
<p class="note">合成データの場合、成績は基盤の動作確認用であり実市場の成績を示すものではありません。</p>
</body></html>"""
    out = results_dir / "report.html"
    out.write_text(html, encoding="utf-8")
    return out
