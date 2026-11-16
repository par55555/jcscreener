"""Hard buy-box filtering. Runs before any API call so you do not burn
RentCast credits on properties you would never buy."""

from __future__ import annotations

from typing import List, Optional, Tuple

from .config import BuyBox
from .models import Listing


def _text(listing: Listing) -> str:
    return " ".join([
        listing.description or "",
        listing.neighborhood or "",
        listing.address or "",
    ]).lower()


def passes(listing: Listing, bb: BuyBox) -> Tuple[bool, Optional[str]]:
    """Return (passed, reason_for_rejection)."""

    if bb.zips and listing.zipcode not in bb.zips:
        return False, f"zip {listing.zipcode or '?'} not in buy box"

    if bb.statuses and listing.status not in bb.statuses:
        return False, f"status '{listing.status}'"

    if not (bb.price_min <= listing.price <= bb.price_max):
        return False, f"price ${listing.price:,.0f} outside ${bb.price_min:,.0f}-${bb.price_max:,.0f}"

    units = int(listing.units or 1)
    if not (bb.units_min <= units <= bb.units_max):
        return False, f"{units} unit(s), want {bb.units_min}-{bb.units_max}"

    if listing.beds is not None and listing.beds < bb.beds_min:
        return False, f"{listing.beds:g} beds < {bb.beds_min:g}"

    if listing.baths is not None and listing.baths < bb.baths_min:
        return False, f"{listing.baths:g} baths < {bb.baths_min:g}"

    if listing.sqft is not None:
        if listing.sqft < bb.sqft_min:
            return False, f"{listing.sqft:,.0f} sqft < {bb.sqft_min:,.0f}"
        if listing.sqft > bb.sqft_max:
            return False, f"{listing.sqft:,.0f} sqft > {bb.sqft_max:,.0f}"

    if listing.year_built is not None and listing.year_built < bb.year_built_min:
        return False, f"built {listing.year_built} < {bb.year_built_min}"

    if listing.days_on_market is not None and listing.days_on_market > bb.max_days_on_market:
        return False, f"{listing.days_on_market} DOM > {bb.max_days_on_market}"

    text = _text(listing)

    if bb.neighborhoods_include:
        if not any(n.lower() in text for n in bb.neighborhoods_include):
            return False, "neighborhood not in include list"

    for n in bb.neighborhoods_exclude:
        if n.lower() in text:
            return False, f"neighborhood excluded ({n})"

    for kw in bb.exclude_keywords:
        if kw.lower() in text:
            return False, f"excluded keyword: '{kw}'"

    return True, None


def find_risks(listing: Listing, bb: BuyBox) -> List[str]:
    """Non-disqualifying flags worth seeing on the report."""
    text = _text(listing)
    risks = [kw for kw in bb.risk_keywords if kw.lower() in text]

    if listing.year_built and listing.year_built < 1978:
        risks.append("pre-1978: lead paint disclosure + RRP-certified contractor required")
    if listing.year_built and listing.year_built < 1940:
        risks.append("pre-1940: assume knob-and-tube / galvanized supply until proven otherwise")
    if int(listing.units or 1) >= 3:
        risks.append("3+ units: JC rent leveling ordinance may apply — verify before offer")
    if not listing.sqft:
        risks.append("no square footage — rehab estimate is unreliable")

    return risks
