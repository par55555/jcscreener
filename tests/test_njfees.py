"""The transfer-tax math is statutory — if these break, a pro-forma is wrong
by thousands of dollars, so they are worth pinning down."""

import math

import pytest

from jcscreener.njfees import (
    graduated_percent_fee,
    jc_annual_property_tax,
    realty_transfer_fee,
    seller_transfer_costs,
)


def test_zero_and_negative():
    assert realty_transfer_fee(0) == 0
    assert realty_transfer_fee(-5) == 0
    assert graduated_percent_fee(0) == 0


def test_below_350k_uses_lower_schedule():
    # 150,000 -> 300 units at $2.00
    assert realty_transfer_fee(150_000) == pytest.approx(600.0)


def test_350k_boundary_jumps_to_higher_schedule():
    """The schedule switch at $350k means the fee is NOT continuous — a dollar
    more consideration costs meaningfully more fee. Sellers price around this."""
    just_under = realty_transfer_fee(350_000)
    just_over = realty_transfer_fee(350_500)
    assert just_over > just_under


def test_monotonic_across_the_range():
    prev = -1.0
    for price in range(50_000, 3_000_000, 25_000):
        fee = realty_transfer_fee(price)
        assert fee >= prev, f"RTF went down at ${price:,}"
        prev = fee


def test_above_350k_bracket_accumulation():
    # 600,000: 150k@2.90 + 50k@4.25 + 350k@4.80 + 50k@5.30
    expected = (300 * 2.90) + (100 * 4.25) + (700 * 4.80) + (100 * 5.30)
    assert realty_transfer_fee(600_000) == pytest.approx(expected, abs=0.01)


def test_mansion_tax_threshold_is_exclusive():
    assert graduated_percent_fee(1_000_000) == 0
    assert graduated_percent_fee(1_000_001) > 0


def test_mansion_tax_is_flat_on_whole_consideration():
    # 1% of the entire price, not just the excess over $1M.
    assert graduated_percent_fee(1_500_000) == pytest.approx(15_000.0)
    assert graduated_percent_fee(2_400_000) == pytest.approx(48_000.0)
    assert graduated_percent_fee(4_000_000) == pytest.approx(140_000.0)


def test_mansion_tax_cliff_is_brutal():
    """$1,000,001 costs ~$10,000 more than $1,000,000. This is why JC listings
    cluster at $999,000."""
    jump = graduated_percent_fee(1_000_001) - graduated_percent_fee(1_000_000)
    assert jump > 9_900


def test_mansion_tax_not_applied_to_nonresidential():
    assert graduated_percent_fee(2_000_000, residential=False) == 0


def test_seller_transfer_costs_sums():
    out = seller_transfer_costs(1_200_000)
    assert out["total"] == pytest.approx(
        out["realty_transfer_fee"] + out["graduated_percent_fee"], abs=0.01
    )
    assert out["graduated_percent_fee"] == pytest.approx(12_000.0)


def test_reduced_schedule_is_cheaper():
    assert realty_transfer_fee(300_000, reduced=True) < realty_transfer_fee(300_000)
    assert realty_transfer_fee(700_000, reduced=True) < realty_transfer_fee(700_000)


def test_jc_property_tax_estimate():
    # 500k market -> 364,100 assessed -> /100 * 2.335
    assert jc_annual_property_tax(500_000) == pytest.approx(8_501.74, abs=1.0)


def test_jc_property_tax_scales_linearly():
    a = jc_annual_property_tax(400_000)
    b = jc_annual_property_tax(800_000)
    assert b == pytest.approx(a * 2, rel=1e-6)
