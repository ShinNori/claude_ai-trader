"""受入テスト・CLI から使う公開API（共通仕様 §8）"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

from . import db as _db
from .backtest import run as _run_backtest
from .strategies import get_strategy


def init_db(home: Path | None = None) -> None:
    _db.init_db(home)


def _load_synth_module():
    """common/synth_data.py を探して読み込む（build-claude/../common）。無ければ同梱コピー。"""
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "common" / "synth_data.py",   # ai-trader/common
        here.parent / "_synth_data_copy.py",
    ]
    for p in candidates:
        if p.exists():
            spec = importlib.util.spec_from_file_location("synth_data", p)
            mod = importlib.util.module_from_spec(spec)
            sys.modules["synth_data"] = mod
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            return mod
    raise FileNotFoundError("common/synth_data.py が見つかりません")


def load_synthetic(home: Path | None = None, seed: int = 42) -> None:
    mod = _load_synth_module()
    data = mod.build(seed=seed)
    _db.init_db(home)
    con = _db.connect(home)
    try:
        for table, df in data.items():
            d = df.copy()
            for c in ("date", "publish_date", "listed_date"):
                if c in d.columns:
                    d[c] = __import__("pandas").to_datetime(d[c]).dt.date
            _db.replace_table(con, table, d)
    finally:
        con.close()


def run_signals(home: Path | None, strategy: str, as_of: date) -> list[dict]:
    con = _db.connect(home, read_only=True)
    try:
        strat = get_strategy(strategy)
        return [c.to_dict() for c in strat.generate(as_of, con)]
    finally:
        con.close()


def run_backtest(home: Path | None, strategy: str, start: date, end: date, out_dir: Path) -> dict:
    return _run_backtest(home, strategy, start, end, Path(out_dir))
