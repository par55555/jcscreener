"""End-to-end behaviour: buy box, pro-forma, scoring, dedup."""

from pathlib import Path

import pytest

from jcscreener import buybox, sources
from jcscreener.alerts import AlertState, select_alerts
from jcscreener.config import load_config
from jcscreener.models import Listing, ScreenResult
from jcscreener.proforma import build_proforma, estimate_rehab
from jcscreener.scoring import evaluate
from jcscreener.screener import screen

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg():
    return load_config(ROOT / "config" / "screener.yaml")


@pytest.fixture
def listings(cfg):
    return sources.load("csv", cfg, path="data/sample_listings.csv")


# --------------------------------------------------------------------------
# Intake
# --------------------------------------------------------------------------

def test_sample_data_loads(listings):
    assert len(listings) >= 12
    assert all(l.address for l in listings)
    assert all(l.price > 0 for l in listings)


def test_messy_columns_normalize():
    l = sources.normalize({
        "Address Line1": "1 Test St", "List Price": "$450,000",
        "Bedrooms": "4", "SquareFootage": "1,900", "Zip": "7307",
        "MLS": "ABC123",
    })
    assert l is not None
    assert l.price == 450_000
    assert l.sqft == 1900
    assert l.zipcode == "07307"      # zero-padded
    assert l.source_id == "ABC123"


def test_rows_without_price_or_address_are_dropped():
    assert sources.normalize({"address": "1 Test St"}) is None
    assert sources.normalize({"price": "400000"}) is None


def test_listing_key_is_stable():
    a = Listing(address="1 Test St", zipcode="07307", price=1)
    b = Listing(address="  1 test st ", zipcode="07307", price=999)
    assert a.key == b.key


# --------------------------------------------------------------------------
# Buy box
# --------------------------------------------------------------------------

def test_buybox_rejects_wrong_zip(cfg):
    bb = cfg.buy_box
    l = Listing(address="1 Test St", zipcode="07302", price=500_000, units=2,
                beds=4, baths=2, sqft=2000, year_built=1920)
    ok, why = buybox.passes(l, bb)
    assert not ok and "zip" in why


def test_buybox_rejects_single_family(cfg):
    l = Listing(address="1 Test St", zipcode="07307", price=500_000, units=1,
                beds=4, baths=2, sqft=2000, year_built=1920)
    ok, why = buybox.passes(l, bb := cfg.buy_box)
    assert not ok and "unit" in why


def test_buybox_rejects_excluded_keywords(cfg):
    l = Listing(address="1 Test St", zipcode="07307", price=500_000, units=2,
                beds=4, baths=2, sqft=2000, year_built=1920,
                description="Tenant occupied, cash only.")
    ok, why = buybox.passes(l, cfg.buy_box)
    assert not ok and "keyword" in why


def test_buybox_accepts_a_good_one(cfg):
    l = Listing(address="1 Test St", zipcode="07307", price=500_000, units=2,
                beds=5, baths=2, sqft=2000, year_built=1920,
                description="Vacant two family.")
    ok, why = buybox.passes(l, cfg.buy_box)
    assert ok, why


def test_pre_1978_flags_lead_risk(cfg):
    l = Listing(address="1 Test St", zipcode="07307", price=500_000, units=2,
                beds=5, baths=2, sqft=2000, year_built=1950)
    risks = buybox.find_risks(l, cfg.buy_box)
    assert any("lead" in r for r in risks)


# --------------------------------------------------------------------------
# Rehab + pro-forma
# --------------------------------------------------------------------------

def test_rehab_scales_with_sqft_and_units(cfg):
    small = Listing(address="a", price=1, sqft=1000, units=1)
    big = Listing(address="b", price=1, sqft=2000, units=3)
    assert estimate_rehab(big, cfg).total > estimate_rehab(small, cfg).total * 2


def test_rehab_keyword_adders_fire(cfg):
    clean = Listing(address="a", price=1, sqft=2000, units=2, description="nice")
    tanked = Listing(address="b", price=1, sqft=2000, units=2,
                     description="Buried oil tank in the rear yard.")
    assert "oil tank" in estimate_rehab(tanked, cfg).keyword_adders
    assert estimate_rehab(tanked, cfg).total > estimate_rehab(clean, cfg).total


def test_proforma_components_sum(cfg):
    l = Listing(address="1 Test St", zipcode="07307", price=450_000, units=2,
                beds=5, baths=2, sqft=2000, year_built=1920)
    pf = build_proforma(l, 800_000, cfg)

    assert pf.acquisition_total == pytest.approx(sum(pf.acquisition_costs.values()), abs=0.02)
    assert pf.holding_total == pytest.approx(sum(pf.holding_costs.values()), abs=0.02)
    assert pf.selling_total == pytest.approx(sum(pf.selling_costs.values()), abs=0.02)

    expected_profit = (pf.arv - pf.selling_total - pf.purchase_price
                       - pf.rehab.total - pf.acquisition_total - pf.holding_total)
    assert pf.profit == pytest.approx(expected_profit, abs=0.02)


def test_breakeven_arv_yields_zero_profit(cfg):
    l = Listing(address="1 Test St", zipcode="07307", price=450_000, units=2,
                beds=5, baths=2, sqft=2000, year_built=1920)
    pf = build_proforma(l, 800_000, cfg)
    # At the breakeven ARV, profit should be ~0 (selling costs shift slightly
    # because commission is a % of ARV, so allow a tolerance band).
    pf2 = build_proforma(l, pf.breakeven_arv, cfg)
    assert abs(pf2.profit) < pf.breakeven_arv * 0.08


def test_longer_hold_costs_more(cfg):
    l = Listing(address="1 Test St", zipcode="07307", price=450_000, units=2,
                beds=5, baths=2, sqft=2000, year_built=1920)
    short = build_proforma(l, 800_000, cfg)
    cfg.raw["underwriting"]["hold_months"] = 14
    long = build_proforma(l, 800_000, cfg)
    cfg.raw["underwriting"]["hold_months"] = 7
    assert long.holding_total > short.holding_total
    assert long.profit < short.profit


def test_mansion_tax_appears_on_high_arv_exit(cfg):
    l = Listing(address="1 Test St", zipcode="07307", price=700_000, units=2,
                beds=5, baths=3, sqft=2600, year_built=1920)
    under = build_proforma(l, 950_000, cfg)
    over = build_proforma(l, 1_100_000, cfg)
    assert under.selling_costs["nj_graduated_percent_fee"] == 0
    assert over.selling_costs["nj_graduated_percent_fee"] == pytest.approx(11_000.0)


def test_mao_is_the_price_that_makes_the_rule_work(cfg):
    l = Listing(address="1 Test St", zipcode="07307", price=450_000, units=2,
                beds=5, baths=2, sqft=2000, year_built=1920)
    pf = build_proforma(l, 800_000, cfg)
    factor = cfg.underwriting.mao_arv_factor
    assert pf.mao == pytest.approx(800_000 * factor - pf.rehab.total, abs=0.02)


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def test_overpriced_deal_passes(cfg):
    l = Listing(address="1 Test St", zipcode="07307", price=860_000, units=2,
                beds=5, baths=3, sqft=2400, year_built=1920)
    pf = build_proforma(l, 900_000, cfg)
    verdict, score, reasons = evaluate(pf, cfg)
    assert verdict == "PASS"


def test_cheap_deal_is_strong(cfg):
    l = Listing(address="1 Test St", zipcode="07307", price=380_000, units=2,
                beds=5, baths=3, sqft=1800, year_built=1920, rehab_tier="cosmetic")
    pf = build_proforma(l, 900_000, cfg)
    verdict, score, reasons = evaluate(pf, cfg)
    assert verdict == "STRONG"
    assert score > 50


# --------------------------------------------------------------------------
# Full screen (offline — uses arv_hint, no API calls)
# --------------------------------------------------------------------------

def test_offline_screen_produces_a_spread_of_verdicts(cfg, listings):
    results = screen(listings, cfg, offline=True, keep_filtered=True, verbose=False)
    verdicts = {r.verdict for r in results}
    assert "FILTERED" in verdicts
    assert "STRONG" in verdicts or "NEGOTIATE" in verdicts
    assert len(results) == len(listings)


def test_results_are_sorted_best_first(cfg, listings):
    results = screen(listings, cfg, offline=True, verbose=False)
    order = {"STRONG": 0, "NEGOTIATE": 1, "PASS": 2, "FILTERED": 3}
    ranks = [order[r.verdict] for r in results]
    assert ranks == sorted(ranks)


def test_no_api_calls_in_offline_mode(cfg, listings, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("offline mode must not touch the network")
    monkeypatch.setattr("jcscreener.screener.make_client", boom)
    screen(listings, cfg, offline=True, verbose=False)


# --------------------------------------------------------------------------
# Dedup / alerting
# --------------------------------------------------------------------------

def _strong(price=400_000, addr="1 Test St"):
    return ScreenResult(
        listing=Listing(address=addr, zipcode="07307", price=price),
        verdict="STRONG", score=90,
    )


def test_alert_fires_once_then_goes_quiet(tmp_path):
    state = AlertState(tmp_path / "seen.json")
    r = _strong()
    assert state.is_new(r, 2.5)[0] is True
    state.record(r)
    assert state.is_new(r, 2.5)[0] is False


def test_price_cut_triggers_a_realert(tmp_path):
    state = AlertState(tmp_path / "seen.json")
    state.record(_strong(price=400_000))
    new, why = state.is_new(_strong(price=360_000), 2.5)
    assert new and "cut" in why


def test_tiny_price_change_does_not_realert(tmp_path):
    state = AlertState(tmp_path / "seen.json")
    state.record(_strong(price=400_000))
    new, _ = state.is_new(_strong(price=399_000), 2.5)   # 0.25% change
    assert not new


def test_verdict_improvement_realerts(tmp_path):
    state = AlertState(tmp_path / "seen.json")
    r = _strong()
    r.verdict = "NEGOTIATE"
    state.record(r)
    r2 = _strong()
    assert state.is_new(r2, 2.5)[0] is True


def test_state_round_trips(tmp_path):
    p = tmp_path / "seen.json"
    s1 = AlertState(p)
    s1.record(_strong())
    s1.save()
    s2 = AlertState(p)
    assert s2.is_new(_strong(), 2.5)[0] is False
