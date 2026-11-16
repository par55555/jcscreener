"""
Verdict logic: STRONG / NEGOTIATE / PASS.

  STRONG    — clears every underwriting threshold at the ASKING price.
              These are rare on the MLS. Act on them same-day.

  NEGOTIATE — fails at asking, but the gap to your max allowable offer is
              inside `negotiate_band_pct`. Worth a call; the report tells you
              the exact number to offer.

  PASS      — the math does not work at any price you would realistically get.

`score` (0-100) ranks deals *within* a verdict so the report sorts sensibly.
"""

from __future__ import annotations

from typing import List, Tuple

from .config import Config
from .models import ProForma


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def evaluate(pf: ProForma, cfg: Config) -> Tuple[str, float, List[str]]:
    uw = cfg.underwriting
    reasons: List[str] = []

    hits_profit = pf.profit >= uw.min_profit_dollars
    hits_roi = pf.roi_pct >= uw.min_roi_pct
    hits_margin = pf.margin_pct >= uw.min_margin_pct
    at_or_under_mao = pf.purchase_price <= pf.mao

    if hits_profit:
        reasons.append(f"Profit ${pf.profit:,.0f} clears the ${uw.min_profit_dollars:,.0f} floor")
    else:
        reasons.append(f"Profit ${pf.profit:,.0f} below the ${uw.min_profit_dollars:,.0f} floor")

    if hits_roi:
        reasons.append(f"ROI {pf.roi_pct:.1f}% on ${pf.cash_in:,.0f} cash in")
    else:
        reasons.append(f"ROI {pf.roi_pct:.1f}% below {uw.min_roi_pct:.1f}% target")

    if hits_margin:
        reasons.append(f"Margin {pf.margin_pct:.1f}% of ARV")
    else:
        reasons.append(f"Margin {pf.margin_pct:.1f}% below {uw.min_margin_pct:.1f}% target")

    if at_or_under_mao:
        reasons.append(f"Asking is ${abs(pf.over_mao_by):,.0f} UNDER your MAO of ${pf.mao:,.0f}")
    else:
        reasons.append(f"Asking is ${pf.over_mao_by:,.0f} over your MAO of ${pf.mao:,.0f}")

    cushion = pf.arv - pf.breakeven_arv
    reasons.append(
        f"ARV can fall ${cushion:,.0f} ({cushion / pf.arv * 100:.1f}%) before breakeven"
        if pf.arv else "No ARV available"
    )

    # --- verdict ---
    if hits_profit and hits_roi and hits_margin and at_or_under_mao:
        verdict = "STRONG"
    else:
        band = pf.mao * (1 + uw.negotiate_band_pct / 100.0)
        # Worth a conversation only if the deal actually works at MAO — i.e.
        # the problem is the price, not the property.
        if pf.purchase_price <= band and pf.mao > 0:
            verdict = "NEGOTIATE"
            reasons.append(
                f"Within {uw.negotiate_band_pct:.0f}% negotiating band — "
                f"open at ${pf.mao * 0.96:,.0f}, walk above ${pf.mao:,.0f}"
            )
        else:
            verdict = "PASS"

    # --- score: blend of the four signals, weighted toward cash outcomes ---
    profit_score = _clamp(pf.profit / max(uw.min_profit_dollars, 1) * 50)
    roi_score = _clamp(pf.roi_pct / max(uw.min_roi_pct, 1) * 50)
    margin_score = _clamp(pf.margin_pct / max(uw.min_margin_pct, 1) * 50)
    mao_score = _clamp((1 - pf.over_mao_by / max(pf.mao, 1)) * 50 + 50)

    score = round(
        0.35 * profit_score + 0.25 * roi_score + 0.20 * margin_score + 0.20 * mao_score, 1
    )

    return verdict, score, reasons
