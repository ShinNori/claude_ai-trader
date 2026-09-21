import argparse
import json
import sys
import os
import stat
from datetime import date
from pathlib import Path
from .api import init_db,load_synthetic,run_signals,run_backtest
from .db import default_home
from .report import render

def _validate_export_path(output, name=None):
    """Check the raw CLI export path before resolve can hide a link."""
    output = Path(output).absolute()
    folder = output if name is None else output/name
    targets = [(part, True) for part in reversed((folder, *folder.parents))]
    if name is not None:
        targets.extend((folder/filename, False) for filename in ('summary.json', 'report.html'))
    for target, directory in targets:
        try:
            info = target.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise ValueError('結果の出力先を安全に確認できません') from None
        unsafe = (stat.S_ISLNK(info.st_mode)
                  or (hasattr(os.path, 'isjunction') and os.path.isjunction(target))
                  or bool(getattr(info, 'st_file_attributes', 0) & 0x400))
        valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if unsafe or not valid:
            raise ValueError('結果の出力先を安全に確認できません')
    return output

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
        from .backtest_exports import export_summary_report
        source_root = Path(__file__).resolve().parents[2]
        output = _validate_export_path(a.out)
        output = output.resolve()
        runtime = home/'results' if output.is_relative_to(source_root) else output
        summary = run_backtest(home,a.strategy,a.start,a.end,runtime)
        if runtime.resolve()!=output:
            name = f'{a.strategy}_{summary["version"]}'
            _validate_export_path(output, name)
            export_summary_report(runtime/name, output/name)
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
