"""
New Jersey transfer-tax math.

Rates transcribed from the NJ Division of Taxation Realty Transfer Fee schedule
(https://www.nj.gov/treasury/taxation/realty.shtml), verified September 2026.

Two separate charges apply on a NJ residential sale:

  1. Realty Transfer Fee (RTF) — a bracketed per-$500 fee. Two schedules exist:
     one for total consideration at or below $350,000 and a higher one above it.
     The brackets are cumulative, not a flat lookup.

  2. Graduated Percent Fee, colloquially the "mansion tax" — applies when
     consideration EXCEEDS $1,000,000 on Class 2 residential, Class 3A farm
     w/ residence, Class 4A commercial, and Class 4C co-op property. It is a
     flat percentage of the WHOLE consideration determined by which bracket the
     price falls in (not marginal).

Who pays: the seller is statutorily responsible for both the RTF and the
Graduated Percent Fee. For a flip that means both land on your HUD at exit,
not at acquisition. `buyer_pays_mansion_tax` in the config exists only because
some title companies still quote it buyer-side out of habit.
"""

from __future__ import annotations

import math
from typing import List, Tuple

# ---------------------------------------------------------------------------
# Realty Transfer Fee brackets: (upper_bound_of_bracket, dollars_per_500)
# `None` upper bound means "everything above".
# ---------------------------------------------------------------------------

_RTF_AT_OR_BELOW_350K: List[Tuple[float | None, float]] = [
    (150_000, 2.00),
    (200_000, 3.35),
    (350_000, 3.90),
]

_RTF_ABOVE_350K: List[Tuple[float | None, float]] = [
    (150_000, 2.90),
    (200_000, 4.25),
    (550_000, 4.80),
    (850_000, 5.30),
    (1_000_000, 5.80),
    (None, 6.05),
]

# Reduced schedule (senior / disabled / blind / low-and-moderate-income housing).
_RTF_REDUCED_AT_OR_BELOW_350K: List[Tuple[float | None, float]] = [
    (150_000, 0.50),
    (350_000, 1.25),
]

_RTF_REDUCED_ABOVE_350K: List[Tuple[float | None, float]] = [
    (150_000, 1.40),
    (550_000, 2.15),
    (850_000, 2.65),
    (1_000_000, 3.15),
    (None, 3.40),
]

# Graduated Percent Fee: (upper_bound_inclusive, rate_on_full_consideration)
_MANSION_BRACKETS: List[Tuple[float | None, float]] = [
    (2_000_000, 0.010),
    (2_500_000, 0.020),
    (3_000_000, 0.025),
    (3_500_000, 0.030),
    (None, 0.035),
]


def _apply_brackets(consideration: float, brackets: List[Tuple[float | None, float]]) -> float:
    """Walk cumulative brackets, charging per $500 (or fraction) in each."""
    if consideration <= 0:
        return 0.0

    fee = 0.0
    lower = 0.0
    for upper, rate_per_500 in brackets:
        cap = consideration if upper is None else min(consideration, upper)
        if cap <= lower:
            continue
        portion = cap - lower
        units = math.ceil(portion / 500.0)
        fee += units * rate_per_500
        lower = cap
        if upper is not None and consideration <= upper:
            break
    return round(fee, 2)


def realty_transfer_fee(consideration: float, reduced: bool = False) -> float:
    """
    Total NJ Realty Transfer Fee on a sale of `consideration` dollars.

    >>> realty_transfer_fee(300_000)
    2445.0
    >>> realty_transfer_fee(650_000) > realty_transfer_fee(550_000)
    True
    """
    if consideration <= 0:
        return 0.0

    if consideration <= 350_000:
        brackets = _RTF_REDUCED_AT_OR_BELOW_350K if reduced else _RTF_AT_OR_BELOW_350K
    else:
        brackets = _RTF_REDUCED_ABOVE_350K if reduced else _RTF_ABOVE_350K

    return _apply_brackets(consideration, brackets)


def graduated_percent_fee(consideration: float, residential: bool = True) -> float:
    """
    NJ Graduated Percent Fee (the "mansion tax").

    Zero at or below $1,000,000. Above that it is a FLAT percentage of the
    entire consideration, stepped by bracket — so $1,000,001 costs ~$10,000,
    which is the classic cliff sellers try to price just under.
    """
    if not residential or consideration <= 1_000_000:
        return 0.0

    for upper, rate in _MANSION_BRACKETS:
        if upper is None or consideration <= upper:
            return round(consideration * rate, 2)
    return 0.0


# Friendlier alias — this is what everyone actually calls it.
mansion_tax = graduated_percent_fee


def seller_transfer_costs(consideration: float, residential: bool = True) -> dict:
    """Both state-level charges a seller owes at closing, itemized."""
    rtf = realty_transfer_fee(consideration)
    gpf = graduated_percent_fee(consideration, residential=residential)
    return {
        "realty_transfer_fee": rtf,
        "graduated_percent_fee": gpf,
        "total": round(rtf + gpf, 2),
    }


def jc_annual_property_tax(
    market_value: float,
    general_tax_rate: float = 2.335,
    equalization_ratio: float = 0.7282,
) -> float:
    """
    Estimate Jersey City annual property tax from a market value.

    JC bills on ASSESSED value at the general tax rate (per $100). Assessments
    run below market; the county equalization ratio is the bridge. This is an
    estimate only — pull the actual tax card before you make an offer, and
    watch for abatements, which distort this badly.
    """
    assessed = market_value * equalization_ratio
    return round((assessed / 100.0) * general_tax_rate, 2)
