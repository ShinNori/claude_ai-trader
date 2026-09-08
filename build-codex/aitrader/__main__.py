import argparse
import json
import sys
from datetime import date
from pathlib import Path
from .api import init_db,load_synthetic,run_signals,run_backtest
from .db import default_home
from .report import render

def main():
    parser = argparse.ArgumentParser(description='Research only: no live signals or orders')
    subs = parser.add_subparsers(dest='command',required=True)
    subs.add_parser('init-db')
    load = subs.add_parser('load-synthetic'); load.add_argument('--seed',type=int,default=42)
    signals = subs.add_parser('signals'); signals.add_argument('--strategy',required=True); signals.add_argument('--as-of',type=date.fromisoformat,required=True)
    packets = subs.add_parser('packets', help='Research proposals only; no AI calls or notifications')
    packets.add_argument('--strategy', required=True)
    packets.add_argument('--as-of', type=date.fromisoformat, required=True)
    packets.add_argument('--budget-per-name', type=int, default=250000)
    packets.add_argument('--policy-version', default='review-v1')
    packets.add_argument('--events', type=Path, help='Instrument-keyed events JSON; missing facts remain UNKNOWN')
    packets.add_argument('--lot-sizes', type=Path, help='Instrument-keyed lot sizes JSON; research default is 100')
    for command in ('backtest','fetch'):
        p = subs.add_parser(command)
        p.add_argument('--from',dest='start',type=date.fromisoformat,required=True)
        p.add_argument('--to',dest='end',type=date.fromisoformat,required=True)
        if command=='backtest':
            p.add_argument('--strategy',required=True); p.add_argument('--out',type=Path,default=Path('results'))
    p = subs.add_parser('report'); p.add_argument('--results',type=Path,required=True)
    a = parser.parse_args(); home = default_home()
    if a.command=='init-db': init_db(home)
    elif a.command=='load-synthetic': load_synthetic(home,a.seed)
    elif a.command=='signals':
        print('研究用候補です。実際の売買シグナルではありません。',file=sys.stderr)
        print(json.dumps(run_signals(home,a.strategy,a.as_of),ensure_ascii=False,default=str))
    elif a.command=='packets':
        from .packet_cli import generate
        proposals = generate(home,a.strategy,a.as_of,a.budget_per_name,a.policy_version,a.events,a.lot_sizes)
        print('研究用判定パケットです。AI審査・通知・発注は行いません。イベント未確認はUNKNOWNです。',file=sys.stderr)
        print(json.dumps(proposals,ensure_ascii=False,default=lambda value:value.isoformat(),allow_nan=False))
    elif a.command=='backtest':
        # Full ledgers live outside the shared source tree; only summaries are copied.
        import shutil
        source_root = Path(__file__).resolve().parents[2]
        output = a.out.resolve()
        runtime = home/'results' if output.is_relative_to(source_root) else output
        summary = run_backtest(home,a.strategy,a.start,a.end,runtime)
        if runtime.resolve()!=output:
            name = f'{a.strategy}_{summary["version"]}'
            (output/name).mkdir(parents=True,exist_ok=True)
            for file in ('summary.json','report.html'):
                shutil.copy2(runtime/name/file,output/name/file)
        print(json.dumps(summary,ensure_ascii=False))
    elif a.command=='report': render(a.results)
    elif a.command=='fetch':
        from .jquants import fetch
        fetch(home,a.start,a.end)

if __name__=='__main__':
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    try: main()
    except (ValueError,RuntimeError) as exc:
        print(str(exc),file=sys.stderr)
        sys.exit(2)
