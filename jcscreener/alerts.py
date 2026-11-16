"""
Dedup-based alerting.

The scheduled scan runs daily against the same market. Without dedup you get
the same eight properties emailed to you every morning until you stop reading
the email. So: a deal is alerted ONCE. It is re-alerted only if its price moves
more than `realert_on_price_change_pct`, or its verdict improves.

State is a plain JSON file so you can open it, read it, and delete a line to
force a re-alert.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from .config import Config
from .models import ScreenResult

_VERDICT_RANK = {"PASS": 0, "FILTERED": 0, "NEGOTIATE": 1, "STRONG": 2}


class AlertState:
    def __init__(self, path: Path):
        self.path = path
        self.data: Dict[str, Dict] = {}
        if path.exists():
            try:
                self.data = json.loads(path.read_text())
            except Exception:
                self.data = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, sort_keys=True))

    def is_new(self, result: ScreenResult, price_change_threshold: float) -> Tuple[bool, str]:
        key = result.listing.key
        prior = self.data.get(key)

        if prior is None:
            return True, "new listing"

        old_price = float(prior.get("price", 0))
        new_price = float(result.listing.price)
        if old_price > 0:
            change_pct = abs(new_price - old_price) / old_price * 100.0
            if change_pct >= price_change_threshold:
                direction = "cut" if new_price < old_price else "raised"
                return True, f"price {direction} {change_pct:.1f}% (${old_price:,.0f} -> ${new_price:,.0f})"

        old_rank = _VERDICT_RANK.get(prior.get("verdict", "PASS"), 0)
        new_rank = _VERDICT_RANK.get(result.verdict, 0)
        if new_rank > old_rank:
            return True, f"verdict improved {prior.get('verdict')} -> {result.verdict}"

        return False, "already alerted"

    def record(self, result: ScreenResult) -> None:
        self.data[result.listing.key] = {
            "address": result.listing.full_address,
            "price": result.listing.price,
            "verdict": result.verdict,
            "score": result.score,
            "profit": result.proforma.profit if result.proforma else None,
            "last_seen": datetime.now().isoformat(timespec="seconds"),
        }


def select_alerts(results: List[ScreenResult], cfg: Config) -> Tuple[List[Tuple[ScreenResult, str]], AlertState]:
    """Return [(result, why_it_is_alertable)] plus the state object to save."""
    out_cfg = cfg.output
    state = AlertState(cfg.resolve(out_cfg.get("state_file", "out/.seen_deals.json")))
    verdicts = set(out_cfg.get("alert_verdicts", ["STRONG"]))
    threshold = float(out_cfg.get("realert_on_price_change_pct", 2.5))

    alerts: List[Tuple[ScreenResult, str]] = []
    for r in results:
        if r.verdict not in verdicts:
            continue
        new, why = state.is_new(r, threshold)
        if new:
            alerts.append((r, why))
        state.record(r)

    alerts.sort(key=lambda t: t[0].score, reverse=True)
    return alerts, state
