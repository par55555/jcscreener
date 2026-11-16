"""
Flip pro-forma for Jersey City.

The whole point of this module is that every dollar is traceable. Nothing is
a magic constant: acquisition, holding and selling costs all come from the
config, and the NJ transfer taxes come from the statutory schedule in njfees.

Call `build_proforma(listing, arv, config)` and you get a ProForma whose
`acquisition_costs` / `holding_costs` / `selling_costs` dicts itemize
everything, so `--explain` can print a real HUD-style breakdown.
"""

from __future__ import annotations

from typing import Dict, Optional

from .config import Config
from .models import Listing, ProForma, RehabEstimate
from .njfees import jc_annual_property_tax, realty_transfer_fee, graduated_percent_fee


# ---------------------------------------------------------------------------
# Rehab
# ---------------------------------------------------------------------------

def estimate_rehab(listing: Listing, cfg: Config) -> RehabEstimate:
    """
    Square-footage-driven rehab estimate with multifamily and risk adders.

    This is the number most likely to be wrong. Replace the tier values in
    config/screener.yaml with your own contractor's actuals as you get them —
    that single change improves screening accuracy more than anything else
    in this tool.
    """
    rehab_cfg = cfg.rehab
    tiers: Dict[str, float] = rehab_cfg.get("tiers", {})
    tier = listing.rehab_tier or rehab_cfg.get("default_tier", "moderate")
    if tier not in tiers:
        tier = rehab_cfg.get("default_tier", "moderate")

    psf = float(tiers.get(tier, 95))
    sqft = float(listing.sqft or 0)
    base_cost = psf * sqft

    notes = []
    if sqft <= 0:
        notes.append("No square footage on the listing — rehab base is $0; treat with suspicion.")

    # Every kitchen and bath past the first unit is real money.
    extra_units = max(0, int(listing.units or 1) - 1)
    unit_adders = extra_units * float(rehab_cfg.get("per_extra_unit", 0))
    if extra_units:
        notes.append(f"{extra_units} extra unit(s) @ ${rehab_cfg.get('per_extra_unit', 0):,.0f}")

    # Keyword-triggered line items.
    haystack = f"{listing.description} {listing.neighborhood}".lower()
    keyword_adders: Dict[str, float] = {}
    for kw, amount in (rehab_cfg.get("adders") or {}).items():
        if kw.lower() in haystack:
            keyword_adders[kw] = float(amount)

    subtotal = base_cost + unit_adders + sum(keyword_adders.values())
    contingency_pct = float(rehab_cfg.get("contingency_pct", 0))
    contingency = subtotal * contingency_pct / 100.0

    return RehabEstimate(
        tier=tier,
        base_psf=psf,
        base_cost=round(base_cost, 2),
        unit_adders=round(unit_adders, 2),
        keyword_adders=keyword_adders,
        contingency=round(contingency, 2),
        total=round(subtotal + contingency, 2),
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Cost blocks
# ---------------------------------------------------------------------------

def _acquisition_costs(price: float, cfg: Config) -> Dict[str, float]:
    c = cfg.costs
    costs = {
        "buyer_attorney": float(c.get("buyer_attorney", 0)),
        "title_insurance": round(price * float(c.get("title_insurance_pct", 0)) / 100.0, 2),
        "title_search_settlement": float(c.get("title_search_and_settlement", 0)),
        "inspection": float(c.get("inspection", 0)),
        "survey": float(c.get("survey", 0)),
        "recording_fees": float(c.get("recording_fees", 0)),
    }

    # NJ moved the Graduated Percent Fee to the seller in 2025; this is here
    # only for the case where a title company still quotes it buyer-side.
    if c.get("buyer_pays_mansion_tax", False):
        costs["mansion_tax_buyer_side"] = graduated_percent_fee(price)

    fin = c.get("financing", {})
    mode = fin.get("mode", "owner_occupied")
    terms = fin.get(mode, {}) if mode != "cash" else {}
    if terms:
        costs["lender_fees"] = float(terms.get("lender_fees", 0))
        points_pct = float(terms.get("points_pct", 0))
        if points_pct:
            down_pct = float(terms.get("down_payment_pct", 0))
            loan = price * (1 - down_pct / 100.0)
            costs["loan_points"] = round(loan * points_pct / 100.0, 2)

    return costs


def _holding_costs(price: float, listing: Listing, cfg: Config, months: int) -> Dict[str, float]:
    c = cfg.costs
    h = c.get("holding", {})

    if listing.taxes_annual:
        annual_tax = float(listing.taxes_annual)
    else:
        annual_tax = jc_annual_property_tax(
            price,
            general_tax_rate=float(h.get("jc_general_tax_rate", 2.335)),
            equalization_ratio=float(h.get("jc_equalization_ratio", 0.7282)),
        )

    fin = c.get("financing", {})
    mode = fin.get("mode", "owner_occupied")
    terms = fin.get(mode, {}) if mode != "cash" else {}

    if terms:
        down_pct = float(terms.get("down_payment_pct", 0))
        loan = price * (1 - down_pct / 100.0)
        rate = float(terms.get("interest_rate_pct", 0)) / 100.0
        # Interest-only carry is the honest way to model a flip hold; principal
        # paydown comes back to you at sale, so it is not a cost.
        monthly_interest = loan * rate / 12.0
        pmi = float(terms.get("pmi_monthly", 0))
    else:
        loan = 0.0
        monthly_interest = 0.0
        pmi = 0.0

    return {
        "loan_interest": round(monthly_interest * months, 2),
        "pmi": round(pmi * months, 2),
        "property_taxes": round(annual_tax / 12.0 * months, 2),
        "insurance": round(float(h.get("insurance_monthly", 0)) * months, 2),
        "utilities": round(float(h.get("utilities_monthly", 0)) * months, 2),
        "lawn_snow": round(float(h.get("lawn_snow_monthly", 0)) * months, 2),
        "misc": round(float(h.get("misc_monthly", 0)) * months, 2),
    }


def _selling_costs(arv: float, cfg: Config) -> Dict[str, float]:
    s = cfg.costs.get("selling", {})
    return {
        "agent_commission": round(arv * float(s.get("agent_commission_pct", 0)) / 100.0, 2),
        "seller_attorney": float(s.get("seller_attorney", 0)),
        "seller_concessions": round(arv * float(s.get("seller_concessions_pct", 0)) / 100.0, 2),
        "staging_marketing": float(s.get("staging_and_marketing", 0)),
        # Statutory, straight from the NJ schedule. Seller pays both.
        "nj_realty_transfer_fee": realty_transfer_fee(arv),
        "nj_graduated_percent_fee": graduated_percent_fee(arv),
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def build_proforma(
    listing: Listing,
    arv: float,
    cfg: Config,
    purchase_price: Optional[float] = None,
    arv_low: Optional[float] = None,
    arv_high: Optional[float] = None,
    arv_confidence: Optional[str] = None,
    rehab: Optional[RehabEstimate] = None,
) -> ProForma:
    uw = cfg.underwriting
    price = float(purchase_price if purchase_price is not None else listing.price)
    months = int(uw.hold_months)

    rehab = rehab or estimate_rehab(listing, cfg)

    acq = _acquisition_costs(price, cfg)
    hold = _holding_costs(price, listing, cfg, months)
    sell = _selling_costs(arv, cfg)

    acq_total = round(sum(acq.values()), 2)
    hold_total = round(sum(hold.values()), 2)
    sell_total = round(sum(sell.values()), 2)

    fin = cfg.costs.get("financing", {})
    mode = fin.get("mode", "owner_occupied")
    terms = fin.get(mode, {}) if mode != "cash" else {}
    down_pct = float(terms.get("down_payment_pct", 100.0)) if terms else 100.0
    down_payment = round(price * down_pct / 100.0, 2)
    loan_amount = round(price - down_payment, 2)

    total_project_cost = round(price + rehab.total + acq_total + hold_total + sell_total, 2)

    # Cash actually out of your pocket — the denominator for ROI.
    cash_in = round(down_payment + rehab.total + acq_total + hold_total, 2)

    net_proceeds = round(arv - sell_total, 2)
    profit = round(net_proceeds - price - rehab.total - acq_total - hold_total, 2)

    roi_pct = round(profit / cash_in * 100.0, 2) if cash_in > 0 else 0.0
    margin_pct = round(profit / arv * 100.0, 2) if arv > 0 else 0.0

    # Max allowable offer: the price at which this deal still clears the rule.
    mao = round(arv * uw.mao_arv_factor - rehab.total, 2)
    over_mao_by = round(price - mao, 2)

    # The ARV at which profit hits zero — how much the market can move against
    # you before this deal is underwater.
    breakeven_arv = round(price + rehab.total + acq_total + hold_total + sell_total, 2)

    return ProForma(
        purchase_price=price,
        arv=round(arv, 2),
        arv_low=arv_low,
        arv_high=arv_high,
        arv_confidence=arv_confidence,
        rehab=rehab,
        down_payment=down_payment,
        loan_amount=loan_amount,
        acquisition_costs=acq,
        acquisition_total=acq_total,
        hold_months=months,
        holding_costs=hold,
        holding_total=hold_total,
        selling_costs=sell,
        selling_total=sell_total,
        total_project_cost=total_project_cost,
        cash_in=cash_in,
        net_proceeds=net_proceeds,
        profit=profit,
        roi_pct=roi_pct,
        margin_pct=margin_pct,
        mao=mao,
        over_mao_by=over_mao_by,
        breakeven_arv=breakeven_arv,
    )
