"""戦略インターフェース（共通仕様 §4）"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Protocol

import duckdb


@dataclass
class Candidate:
    code: str
    side: str
    weight: float
    score: float
    reason: str
    strategy: str
    strategy_version: str
    as_of: date
    holding_days: int
    limit_pct: float

    def to_dict(self) -> dict:
        d = asdict(self)
        d["as_of"] = self.as_of.isoformat()
        return d


class Strategy(Protocol):
    name: str
    version: str
    rebalance: str
    holding_days: int
    limit_pct: float

    def universe(self, as_of: date, con: duckdb.DuckDBPyConnection) -> list[str]: ...
    def generate(self, as_of: date, con: duckdb.DuckDBPyConnection) -> list[Candidate]: ...
