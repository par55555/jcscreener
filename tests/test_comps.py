"""Comps and ARV. Uses a stub client — no network, no API key, no cost."""

from pathlib import Path

import pytest

from jcscreener.comps import ArvResult, CompPool, apply_arv_policy, compute_arv
from jcscreener.config import load_config
from jcscreener.models import Listing

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg():
    return load_config(ROOT / "config" / "screener.yaml")


def _comp(price, sqft, beds=5):
    return {"price": price, "squareFootage": sqft, "bedrooms": beds}


def _pool(n=12, psf=400, sqft=2000):
    """n comps all priced at roughly `psf` per square foot."""
    return [_comp(int(psf * sqft * (1 + (i - n / 2) * 0.01)), sqft) for i in range(n)]


class StubClient:
    """Stands in for RentCastClient and counts calls, so the CompPool's
    'one call per area' promise is actually asserted rather than assumed."""

    def __init__(self, rows_by_zip):
        self.rows_by_zip = rows_by_zip
        self.call_count = 0

    def sale_listings(self, zipcode, limit=500, status="Inactive", days_old=270):
        self.call_count += 1
        return self.rows_by_zip.get(zipcode, [])


# --------------------------------------------------------------------------
# compute_arv
# --------------------------------------------------------------------------

def test_arv_tracks_median_price_per_sqft():
    subject = Listing(address="a", price=1, sqft=2000, units=1, beds=5)
    res = compute_arv(subject, _pool(psf=400, sqft=2000))
    assert res.arv == pytest.approx(800_000, rel=0.05)
    assert res.confidence in ("high", "medium")


def test_arv_scales_with_subject_size():
    small = Listing(address="a", price=1, sqft=1500, units=1, beds=5)
    big = Listing(address="b", price=1, sqft=2500, units=1, beds=5)
    comps = _pool(psf=400, sqft=2000)
    assert compute_arv(big, comps).arv > compute_arv(small, comps).arv


def test_multifamily_adjustment_reduces_arv():
    two = Listing(address="a", price=1, sqft=2000, units=2, beds=5)
    four = Listing(address="b", price=1, sqft=2000, units=4, beds=5)
    comps = _pool(psf=400, sqft=2000)
    assert compute_arv(four, comps).arv < compute_arv(two, comps).arv


def test_outliers_are_rejected():
    subject = Listing(address="a", price=1, sqft=2000, units=1, beds=5)
    comps = _pool(psf=400, sqft=2000)
    clean = compute_arv(subject, comps).arv
    # Add absurd data points that a naive mean would swallow.
    comps += [_comp(50_000, 2000), _comp(9_000_000, 2000)]
    assert compute_arv(subject, comps).arv == pytest.approx(clean, rel=0.10)


def test_size_mismatched_comps_are_dropped():
    subject = Listing(address="a", price=1, sqft=2000, units=1, beds=5)
    # All comps are 5x the subject's size — none should qualify.
    res = compute_arv(subject, [_comp(4_000_000, 10_000) for _ in range(10)])
    assert res.arv == 0
    assert res.confidence == "low"


def test_too_few_comps_returns_low_confidence_zero():
    subject = Listing(address="a", price=1, sqft=2000, units=1, beds=5)
    res = compute_arv(subject, _pool(n=2, sqft=2000))
    assert res.arv == 0
    assert "usable comp" in res.notes[0]


def test_no_sqft_subject_cannot_be_valued():
    subject = Listing(address="a", price=1, sqft=None, units=1)
    assert compute_arv(subject, _pool()).arv == 0


def test_wide_spread_lowers_confidence():
    subject = Listing(address="a", price=1, sqft=2000, units=1, beds=5)
    tight = compute_arv(subject, _pool(n=12, psf=400, sqft=2000))
    wild = [_comp(int(p * 2000), 2000) for p in (220, 280, 340, 400, 460, 520, 580, 640)]
    loose = compute_arv(subject, wild)
    assert loose.confidence in ("medium", "low")
    assert tight.confidence == "high"


# --------------------------------------------------------------------------
# CompPool — the API-call economics
# --------------------------------------------------------------------------

def test_pool_makes_one_call_per_area_not_per_property(cfg):
    rows = {"07307": _pool(n=20, psf=420, sqft=1900),
            "07306": _pool(n=20, psf=380, sqft=2100)}
    client = StubClient(rows)
    pool = CompPool(client, cfg).load(["07307", "07306"])

    subjects = [
        Listing(address=f"{i} Test St", zipcode="07307" if i % 2 else "07306",
                price=500_000, sqft=2000, units=2, beds=5)
        for i in range(40)
    ]
    valued = [pool.estimate(s) for s in subjects]

    assert client.call_count == 2, "pool should cost one call per ZIP, not per property"
    assert all(v.arv > 0 for v in valued)


def test_pool_does_not_refetch_a_loaded_area(cfg):
    client = StubClient({"07307": _pool()})
    pool = CompPool(client, cfg)
    pool.load(["07307"])
    pool.load(["07307"])
    assert client.call_count == 1


def test_pool_without_data_returns_low_confidence(cfg):
    client = StubClient({})
    pool = CompPool(client, cfg).load(["07399"])
    res = pool.estimate(Listing(address="a", zipcode="07399", price=1, sqft=2000, units=2))
    assert res.arv == 0
    assert res.confidence == "low"


# --------------------------------------------------------------------------
# ARV policy
# --------------------------------------------------------------------------

def test_conservative_mode_uses_the_low_end(cfg):
    res = ArvResult(arv=800_000, low=740_000, high=860_000, confidence="medium")
    cfg.raw["underwriting"]["arv_mode"] = "conservative"
    value, _ = apply_arv_policy(res, cfg)
    assert value < 740_000          # low end, then the haircut


def test_optimistic_mode_is_higher_than_conservative(cfg):
    res = ArvResult(arv=800_000, low=740_000, high=860_000)
    cfg.raw["underwriting"]["arv_mode"] = "optimistic"
    high, _ = apply_arv_policy(res, cfg)
    cfg.raw["underwriting"]["arv_mode"] = "conservative"
    low, _ = apply_arv_policy(res, cfg)
    assert high > low


def test_haircut_is_applied(cfg):
    res = ArvResult(arv=800_000, low=800_000, high=800_000)
    cfg.raw["underwriting"]["arv_mode"] = "conservative"
    cfg.raw["underwriting"]["arv_haircut_pct"] = 10.0
    value, _ = apply_arv_policy(res, cfg)
    assert value == pytest.approx(720_000)
    cfg.raw["underwriting"]["arv_haircut_pct"] = 3.0


def test_policy_falls_back_to_point_estimate_without_a_range(cfg):
    res = ArvResult(arv=800_000, low=None, high=None)
    cfg.raw["underwriting"]["arv_mode"] = "conservative"
    value, _ = apply_arv_policy(res, cfg)
    assert value == pytest.approx(800_000 * 0.97)
