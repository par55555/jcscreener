"""
ARV estimation.

Two paths, and the difference matters for your API bill:

  1. Per-property AVM  — one RentCast /avm/value call per subject property.
     Most accurate, but N properties = N calls.

  2. CompPool          — ONE /listings/sale call pulls every recent sale in an
     area, then every subject property in that area is valued locally against
     that pool. N properties = 1 call. This is what `use_pool: true` turns on
     and it is what you want for a nightly area sweep.

Responses are cached on disk (config: comps.cache_hours) so re-running a scan
the same day costs nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from .config import Config
from .models import Listing

RENTCAST_BASE = "https://api.rentcast.io/v1"


class CompsError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Disk cache
# ---------------------------------------------------------------------------

class _Cache:
    def __init__(self, root: Path, ttl_hours: float):
        self.root = root
        self.ttl = ttl_hours * 3600
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        h = hashlib.sha1(key.encode()).hexdigest()[:20]
        return self.root / f"{h}.json"

    def get(self, key: str) -> Optional[Any]:
        p = self._path(key)
        if not p.exists():
            return None
        if self.ttl and (time.time() - p.stat().st_mtime) > self.ttl:
            return None
        try:
            return json.loads(p.read_text())
        except Exception:
            return None

    def put(self, key: str, value: Any) -> None:
        try:
            self._path(key).write_text(json.dumps(value))
        except Exception:
            pass  # cache failures are never fatal


# ---------------------------------------------------------------------------
# RentCast client
# ---------------------------------------------------------------------------

class RentCastClient:
    def __init__(self, api_key: Optional[str] = None, cache: Optional[_Cache] = None,
                 timeout: int = 30):
        self.api_key = api_key or os.environ.get("RENTCAST_API_KEY", "")
        if not self.api_key:
            raise CompsError(
                "RENTCAST_API_KEY is not set. Copy .env.example to .env and put your "
                "key there, or export it in your shell. Run with --offline to skip "
                "comps entirely and use the arv_hint column instead."
            )
        self.cache = cache
        self.timeout = timeout
        self._calls = 0

    @property
    def call_count(self) -> int:
        return self._calls

    def _get(self, path: str, params: Dict[str, Any]) -> Any:
        key = f"{path}?{json.dumps(params, sort_keys=True)}"
        if self.cache:
            hit = self.cache.get(key)
            if hit is not None:
                return hit

        resp = requests.get(
            f"{RENTCAST_BASE}{path}",
            params={k: v for k, v in params.items() if v is not None},
            headers={"X-Api-Key": self.api_key, "Accept": "application/json"},
            timeout=self.timeout,
        )
        self._calls += 1

        if resp.status_code == 401:
            raise CompsError("RentCast rejected the API key (401). Rotate/replace it in .env.")
        if resp.status_code == 429:
            raise CompsError("RentCast rate limit hit (429). Wait, or enable use_pool to cut calls.")
        if not resp.ok:
            raise CompsError(f"RentCast {path} returned {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        if self.cache:
            self.cache.put(key, data)
        return data

    def avm_value(self, listing: Listing, comp_count: int = 12,
                  max_radius: float = 1.0, days_old: int = 270) -> Dict[str, Any]:
        """Per-property automated valuation. One API call."""
        return self._get("/avm/value", {
            "address": listing.full_address,
            "propertyType": _rentcast_property_type(listing),
            "bedrooms": listing.beds,
            "bathrooms": listing.baths,
            "squareFootage": listing.sqft,
            "compCount": comp_count,
            "maxRadius": max_radius,
            "daysOld": days_old,
        })

    def sale_listings(self, zipcode: str, limit: int = 500,
                      status: str = "Inactive", days_old: int = 270) -> List[Dict[str, Any]]:
        """
        Every recent sale in a ZIP. One API call feeds an entire CompPool.

        status="Inactive" on the sale-listings endpoint is RentCast's way of
        saying "off market" — i.e. sold. That is what you want to comp against.
        """
        data = self._get("/listings/sale", {
            "zipCode": zipcode,
            "status": status,
            "daysOld": days_old,
            "limit": min(limit, 500),
        })
        return data if isinstance(data, list) else data.get("listings", [])


def _rentcast_property_type(listing: Listing) -> str:
    units = int(listing.units or 1)
    if units >= 2:
        return "Multi-Family"
    t = (listing.property_type or "").lower()
    if "condo" in t:
        return "Condo"
    if "town" in t:
        return "Townhouse"
    return "Single Family"


# ===========================================================================
# ###########################################################################
# ##                                                                       ##
# ##   compute_arv()  —  THIS IS THE FUNCTION YOU WILL WANT TO CHANGE.     ##
# ##                                                                       ##
# ##   Everything else in this file is plumbing. This is the valuation     ##
# ##   opinion. If you disagree with how a comp set turns into an ARV —    ##
# ##   the adjustments, the trimming, the choice of median over mean —     ##
# ##   change it here and nothing else breaks.                             ##
# ##                                                                       ##
# ###########################################################################
# ===========================================================================

@dataclass
class ArvResult:
    arv: float
    low: Optional[float] = None
    high: Optional[float] = None
    confidence: str = "unknown"      # high | medium | low | unknown
    method: str = ""                 # avm | pool | hint
    comp_count: int = 0
    notes: List[str] = None

    def __post_init__(self):
        if self.notes is None:
            self.notes = []


def compute_arv(
    subject: Listing,
    comps: List[Dict[str, Any]],
    *,
    method: str = "pool",
) -> ArvResult:
    """
    Turn a set of comparable sales into an after-repair value for `subject`.

    Method, deliberately simple and auditable:
      1. Keep comps that are plausibly comparable (type, size band, bed count).
      2. Compute each comp's price-per-square-foot.
      3. Trim the outer 10% on each side to kill outliers.
      4. Take the median $/sqft and apply it to the subject's square footage.
      5. Adjust for unit count — a 3-family trades at a different $/sqft than
         a 2-family even at identical size.
      6. Return a range from the interquartile spread of the comp $/sqft.

    Deliberately NOT done here: condition adjustment. These are ARV comps, so
    they should already be renovated sales. If your comp set is full of
    distressed sales, your ARV will be low and you will pass on good deals —
    tighten the filter rather than fudging a multiplier.
    """
    notes: List[str] = []

    sq = float(subject.sqft or 0)
    if sq <= 0:
        return ArvResult(arv=0.0, confidence="low", method=method,
                         notes=["Subject has no square footage; cannot compute $/sqft ARV."])

    subject_units = int(subject.units or 1)
    psfs: List[float] = []

    for c in comps:
        price = c.get("price") or c.get("lastSalePrice")
        csq = c.get("squareFootage")
        if not price or not csq or csq <= 0:
            continue

        # --- comparability gates ---
        # Size within +/- 40% of subject.
        if not (0.60 * sq <= csq <= 1.40 * sq):
            continue
        # Bed count within 1, when both are known.
        if subject.beds and c.get("bedrooms"):
            if abs(float(c["bedrooms"]) - float(subject.beds)) > 1.5:
                continue

        psf = float(price) / float(csq)
        # Sanity band for Hudson County. Anything outside is bad data.
        if not (150 <= psf <= 1200):
            continue

        psfs.append(psf)

    if len(psfs) < 3:
        return ArvResult(
            arv=0.0, confidence="low", method=method, comp_count=len(psfs),
            notes=[f"Only {len(psfs)} usable comp(s) after filtering — not enough to value."],
        )

    psfs.sort()

    # Trim outer 10% each side once there are enough comps to afford it.
    if len(psfs) >= 8:
        cut = max(1, int(len(psfs) * 0.10))
        trimmed = psfs[cut:-cut]
        notes.append(f"Trimmed {cut} comp(s) from each tail.")
    else:
        trimmed = psfs

    median_psf = statistics.median(trimmed)

    # Unit-count adjustment. Multifamily per-sqft pricing is not flat.
    unit_factor = {1: 1.00, 2: 1.00, 3: 0.97, 4: 0.94}.get(subject_units, 0.92)
    if unit_factor != 1.00:
        notes.append(f"{subject_units}-unit adjustment: x{unit_factor}")

    arv = median_psf * sq * unit_factor

    # Range from the interquartile spread.
    try:
        q1, q3 = statistics.quantiles(trimmed, n=4)[0], statistics.quantiles(trimmed, n=4)[2]
    except Exception:
        q1, q3 = min(trimmed), max(trimmed)
    low = q1 * sq * unit_factor
    high = q3 * sq * unit_factor

    spread = (high - low) / arv if arv else 1.0
    confidence = "high" if spread < 0.18 else "medium" if spread < 0.32 else "low"

    notes.append(f"Median ${median_psf:,.0f}/sqft from {len(trimmed)} comps.")

    return ArvResult(
        arv=round(arv, 2), low=round(low, 2), high=round(high, 2),
        confidence=confidence, method=method, comp_count=len(trimmed), notes=notes,
    )


# ---------------------------------------------------------------------------
# CompPool — one API call per area, reused for every subject in that area
# ---------------------------------------------------------------------------

class CompPool:
    """
    Batch ARV estimation.

        pool = CompPool(client, cfg)
        pool.load(["07307", "07306"])        # 2 API calls, total
        for listing in listings:
            result = pool.estimate(listing)  # 0 API calls each

    Contrast with the per-property path, which is one call per listing. On a
    200-listing sweep that is 2 calls instead of 200.
    """

    def __init__(self, client: RentCastClient, cfg: Config):
        self.client = client
        self.cfg = cfg
        self._pools: Dict[str, List[Dict[str, Any]]] = {}

    def load(self, zipcodes: List[str]) -> "CompPool":
        comps_cfg = self.cfg.comps
        for z in zipcodes:
            if z in self._pools:
                continue
            try:
                self._pools[z] = self.client.sale_listings(
                    z, days_old=int(comps_cfg.get("days_old", 270))
                )
            except CompsError:
                raise
            except Exception as exc:  # network blips should not kill a whole sweep
                self._pools[z] = []
                print(f"  ! comp pool for {z} failed: {exc}")
        return self

    def pool_size(self, zipcode: str) -> int:
        return len(self._pools.get(zipcode, []))

    def estimate(self, listing: Listing) -> ArvResult:
        comps = self._pools.get(listing.zipcode, [])
        if not comps:
            return ArvResult(arv=0.0, confidence="low", method="pool",
                             notes=[f"No comp pool loaded for {listing.zipcode}."])
        return compute_arv(listing, comps, method="pool")


# ---------------------------------------------------------------------------
# Top-level helpers
# ---------------------------------------------------------------------------

def make_client(cfg: Config) -> RentCastClient:
    cache_dir = cfg.resolve(".cache/rentcast")
    cache = _Cache(cache_dir, float(cfg.comps.get("cache_hours", 24)))
    return RentCastClient(cache=cache)


def estimate_single(listing: Listing, cfg: Config,
                    client: Optional[RentCastClient] = None) -> ArvResult:
    """Per-property AVM. One API call."""
    client = client or make_client(cfg)
    c = cfg.comps
    data = client.avm_value(
        listing,
        comp_count=int(c.get("comp_count", 12)),
        max_radius=float(c.get("max_radius_miles", 1.0)),
        days_old=int(c.get("days_old", 270)),
    )

    price = data.get("price")
    if not price:
        return ArvResult(arv=0.0, confidence="low", method="avm",
                         notes=["RentCast returned no value for this address."])

    low = data.get("priceRangeLow")
    high = data.get("priceRangeHigh")
    comparables = data.get("comparables") or []

    spread = ((high - low) / price) if (low and high and price) else 1.0
    confidence = "high" if spread < 0.18 else "medium" if spread < 0.32 else "low"

    return ArvResult(
        arv=float(price), low=low, high=high, confidence=confidence,
        method="avm", comp_count=len(comparables),
        notes=[f"RentCast AVM, {len(comparables)} comparables."],
    )


def apply_arv_policy(result: ArvResult, cfg: Config) -> Tuple[float, str]:
    """
    Turn a raw ARV range into the single number you underwrite against,
    per `underwriting.arv_mode` and `arv_haircut_pct`.

    Underwriting off the optimistic end of an AVM range is how people lose
    money on flips. The default is deliberately conservative.
    """
    uw = cfg.underwriting
    mode = (uw.arv_mode or "conservative").lower()

    if mode == "conservative" and result.low:
        base = float(result.low)
    elif mode == "optimistic" and result.high:
        base = float(result.high)
    elif mode == "midpoint" and result.low and result.high:
        base = (float(result.low) + float(result.high)) / 2.0
    else:
        base = float(result.arv)

    haircut = float(uw.arv_haircut_pct or 0)
    final = base * (1 - haircut / 100.0)
    note = f"{mode} ARV ${base:,.0f} less {haircut:.0f}% haircut"
    return round(final, 2), note
