"""戦略レジストリ。config/strategies.yaml のパラメータで上書きできる。"""
from __future__ import annotations

from pathlib import Path

import yaml

from .base import Candidate, Strategy
from .margin_bucket_long import MarginBucketLong

_REGISTRY = {
    MarginBucketLong.name: MarginBucketLong,
}


def load_params(name: str, config_path: Path | None = None) -> dict:
    path = config_path or Path(__file__).resolve().parents[2] / "config" / "strategies.yaml"
    if not path.exists():
        return {}
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return (cfg.get("strategies", {}).get(name, {}) or {}).get("params", {}) or {}


def get_strategy(name: str, config_path: Path | None = None) -> Strategy:
    if name not in _REGISTRY:
        raise KeyError(f"unknown strategy: {name} (available: {list(_REGISTRY)})")
    return _REGISTRY[name](load_params(name, config_path))


__all__ = ["Candidate", "Strategy", "get_strategy", "load_params"]
