"""
Source-agnostic listing intake.

The screener does not care where listings come from. Register a loader, return
a list of `Listing`, done. Three ship in the box (CSV, JSON, RentCast active
listings); add an MLS export, a scraper, or an ADK agent's output the same way.

    @register("my_source")
    def load_my_source(cfg, **kwargs) -> List[Listing]:
        ...
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .config import Config
from .models import Listing

_LOADERS: Dict[str, Callable[..., List[Listing]]] = {}


def register(name: str):
    def deco(fn):
        _LOADERS[name] = fn
        return fn
    return deco


def available() -> List[str]:
    return sorted(_LOADERS)


def load(source: str, cfg: Config, **kwargs) -> List[Listing]:
    if source not in _LOADERS:
        raise ValueError(f"Unknown source '{source}'. Available: {', '.join(available())}")
    return _LOADERS[source](cfg, **kwargs)


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

_FLOAT_FIELDS = {"price", "beds", "baths", "sqft", "lot_sqft", "taxes_annual",
                 "latitude", "longitude", "arv_hint"}
_INT_FIELDS = {"units", "year_built", "days_on_market"}

_ALIASES = {
    "list_price": "price", "listprice": "price", "asking": "price",
    "bedrooms": "beds", "bed": "beds",
    "bathrooms": "baths", "bath": "baths",
    "squarefootage": "sqft", "square_feet": "sqft", "livingarea": "sqft",
    "lotsize": "lot_sqft", "lot_size": "lot_sqft",
    "zip": "zipcode", "postalcode": "zipcode", "postal_code": "zipcode",
    "yearbuilt": "year_built",
    "dom": "days_on_market", "daysonmarket": "days_on_market",
    "remarks": "description", "public_remarks": "description",
    "propertytype": "property_type",
    "mls": "source_id", "mls_id": "source_id", "mlsnumber": "source_id",
    "taxes": "taxes_annual", "annual_taxes": "taxes_annual",
    "addressline1": "address", "address_line1": "address", "street": "address",
}

_VALID = set(Listing.__dataclass_fields__)


def _clean_number(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace("$", "").replace(",", "").replace("%", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def normalize(row: Dict[str, Any], source: str = "csv") -> Optional[Listing]:
    """Map an arbitrary dict onto a Listing, tolerating messy column names."""
    data: Dict[str, Any] = {}

    for raw_key, value in row.items():
        if raw_key is None:
            continue
        key = str(raw_key).strip().lower().replace(" ", "_")
        key = _ALIASES.get(key, key)
        if key not in _VALID:
            continue

        if key in _FLOAT_FIELDS:
            data[key] = _clean_number(value)
        elif key in _INT_FIELDS:
            n = _clean_number(value)
            data[key] = int(n) if n is not None else None
        else:
            data[key] = str(value).strip() if value is not None else ""

    if not data.get("address") or not data.get("price"):
        return None

    data.setdefault("source", source)
    if data.get("zipcode"):
        data["zipcode"] = str(data["zipcode"]).split("-")[0].strip().zfill(5)
    if not data.get("units"):
        data["units"] = 1

    return Listing(**{k: v for k, v in data.items() if v is not None or k in ("description",)})


# ---------------------------------------------------------------------------
# Built-in loaders
# ---------------------------------------------------------------------------

@register("csv")
def load_csv(cfg: Config, path: Optional[str] = None, **_) -> List[Listing]:
    target = cfg.resolve(path or cfg.sources.get("csv", {}).get("path", "data/listings.csv"))
    if not Path(target).exists():
        raise FileNotFoundError(f"No listings CSV at {target}")

    out: List[Listing] = []
    with open(target, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            listing = normalize(row, source="csv")
            if listing:
                out.append(listing)
    return out


@register("json")
def load_json(cfg: Config, path: Optional[str] = None, **_) -> List[Listing]:
    target = cfg.resolve(path or cfg.sources.get("json", {}).get("path", "data/listings.json"))
    if not Path(target).exists():
        raise FileNotFoundError(f"No listings JSON at {target}")

    payload = json.loads(Path(target).read_text())
    rows = payload if isinstance(payload, list) else payload.get("listings", [])
    return [l for l in (normalize(r, source="json") for r in rows) if l]


@register("rentcast")
def load_rentcast(cfg: Config, zips: Optional[List[str]] = None, limit: int = 500, **_) -> List[Listing]:
    """Pull ACTIVE for-sale listings straight from RentCast. Costs API calls."""
    from .comps import make_client

    client = make_client(cfg)
    zips = zips or cfg.buy_box.zips
    out: List[Listing] = []

    for z in zips:
        rows = client.sale_listings(z, limit=limit, status="Active",
                                    days_old=int(cfg.buy_box.max_days_on_market))
        for r in rows:
            listing = normalize({
                "address": r.get("formattedAddress") or r.get("addressLine1"),
                "city": r.get("city"), "state": r.get("state"), "zipcode": r.get("zipCode"),
                "price": r.get("price"),
                "beds": r.get("bedrooms"), "baths": r.get("bathrooms"),
                "sqft": r.get("squareFootage"), "lot_sqft": r.get("lotSize"),
                "year_built": r.get("yearBuilt"),
                "property_type": r.get("propertyType"),
                "status": r.get("status", "Active"),
                "days_on_market": r.get("daysOnMarket"),
                "latitude": r.get("latitude"), "longitude": r.get("longitude"),
                "source_id": r.get("id"),
                "description": r.get("description") or "",
            }, source="rentcast")
            if listing:
                # RentCast does not reliably expose unit count; infer from type.
                if "multi" in (listing.property_type or "").lower() and listing.units < 2:
                    listing.units = 2
                out.append(listing)

    return out
