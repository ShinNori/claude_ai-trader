from dataclasses import dataclass
from datetime import date
from typing import Protocol

@dataclass(frozen=True)
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

class Strategy(Protocol):
    def universe(self, as_of, db): ...
    def generate(self, as_of, db): ...
