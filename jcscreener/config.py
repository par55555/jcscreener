"""Configuration loading. One YAML file in, typed objects out."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import yaml

DEFAULT_CONFIG_PATH = "config/screener.yaml"


class ConfigError(RuntimeError):
    pass


@dataclass
class BuyBox:
    name: str = "default"
    zips: List[str] = field(default_factory=list)
    neighborhoods_include: List[str] = field(default_factory=list)
    neighborhoods_exclude: List[str] = field(default_factory=list)
    price_min: float = 0.0
    price_max: float = 10_000_000.0
    units_min: int = 1
    units_max: int = 99
    beds_min: float = 0.0
    baths_min: float = 0.0
    sqft_min: float = 0.0
    sqft_max: float = 1_000_000.0
    year_built_min: int = 1700
    max_days_on_market: int = 3650
    statuses: List[str] = field(default_factory=list)
    exclude_keywords: List[str] = field(default_factory=list)
    risk_keywords: List[str] = field(default_factory=list)


@dataclass
class Underwriting:
    mao_arv_factor: float = 0.75
    min_profit_dollars: float = 45000
    min_roi_pct: float = 15.0
    min_margin_pct: float = 12.0
    hold_months: int = 7
    negotiate_band_pct: float = 12.0
    arv_mode: str = "conservative"
    arv_haircut_pct: float = 3.0


@dataclass
class Config:
    raw: Dict[str, Any]
    path: Path

    @property
    def buy_box(self) -> BuyBox:
        return BuyBox(**{k: v for k, v in self.raw.get("buy_box", {}).items()
                         if k in BuyBox.__dataclass_fields__})

    @property
    def underwriting(self) -> Underwriting:
        return Underwriting(**{k: v for k, v in self.raw.get("underwriting", {}).items()
                               if k in Underwriting.__dataclass_fields__})

    @property
    def rehab(self) -> Dict[str, Any]:
        return self.raw.get("rehab", {})

    @property
    def costs(self) -> Dict[str, Any]:
        return self.raw.get("costs", {})

    @property
    def comps(self) -> Dict[str, Any]:
        return self.raw.get("comps", {})

    @property
    def sources(self) -> Dict[str, Any]:
        return self.raw.get("sources", {})

    @property
    def output(self) -> Dict[str, Any]:
        return self.raw.get("output", {})

    @property
    def root(self) -> Path:
        """Project root — paths in the config resolve relative to this."""
        return self.path.parent.parent

    def resolve(self, relative: str) -> Path:
        p = Path(relative)
        return p if p.is_absolute() else (self.root / p)


def load_config(path: str | os.PathLike | None = None) -> Config:
    cfg_path = Path(path or os.environ.get("JC_CONFIG", DEFAULT_CONFIG_PATH))
    if not cfg_path.is_absolute():
        # Try cwd, then the package's own project root.
        candidates = [Path.cwd() / cfg_path,
                      Path(__file__).resolve().parent.parent / cfg_path]
        for c in candidates:
            if c.exists():
                cfg_path = c
                break

    if not cfg_path.exists():
        raise ConfigError(
            f"Config not found at {cfg_path}. Run from the project folder, or "
            f"pass --config /path/to/screener.yaml"
        )

    with open(cfg_path, "r") as fh:
        raw = yaml.safe_load(fh) or {}

    return Config(raw=raw, path=cfg_path.resolve())
