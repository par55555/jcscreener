"""Core data structures. Deliberately plain dataclasses — easy to serialize,
easy to feed into an ADK agent later without dragging a framework along."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Any, Dict, List, Optional


@dataclass
class Listing:
    """A candidate property, normalized away from whatever source produced it."""

    address: str
    city: str = "Jersey City"
    state: str = "NJ"
    zipcode: str = ""
    price: float = 0.0

    beds: Optional[float] = None
    baths: Optional[float] = None
    sqft: Optional[float] = None
    lot_sqft: Optional[float] = None
    units: int = 1
    year_built: Optional[int] = None
    property_type: str = "Multi-Family"

    status: str = "Active"
    days_on_market: Optional[int] = None
    list_date: Optional[str] = None
    neighborhood: str = ""
    description: str = ""

    latitude: Optional[float] = None
    longitude: Optional[float] = None

    # Per-property overrides that beat the config defaults when present.
    taxes_annual: Optional[float] = None
    rehab_tier: Optional[str] = None
    # Set this to skip comps entirely for one property — a broker's BPO, an
    # appraisal, or your own number. Always wins over the model.
    arv_hint: Optional[float] = None

    source: str = "csv"
    source_id: str = ""
    url: str = ""

    @property
    def key(self) -> str:
        """Stable identity for dedup. Address+zip, normalized."""
        if self.source_id:
            raw = f"{self.source}:{self.source_id}"
        else:
            raw = f"{self.address.strip().lower()}|{self.zipcode.strip()}"
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    @property
    def full_address(self) -> str:
        return f"{self.address}, {self.city}, {self.state} {self.zipcode}".strip()

    @property
    def price_per_sqft(self) -> Optional[float]:
        if self.sqft and self.sqft > 0:
            return round(self.price / self.sqft, 2)
        return None


@dataclass
class RehabEstimate:
    tier: str
    base_psf: float
    base_cost: float
    unit_adders: float
    keyword_adders: Dict[str, float] = field(default_factory=dict)
    contingency: float = 0.0
    total: float = 0.0
    notes: List[str] = field(default_factory=list)


@dataclass
class ProForma:
    """Full flip economics for one listing at one assumed purchase price."""

    purchase_price: float
    arv: float
    arv_low: Optional[float] = None
    arv_high: Optional[float] = None
    arv_confidence: Optional[str] = None

    rehab: Optional[RehabEstimate] = None

    # Acquisition
    down_payment: float = 0.0
    loan_amount: float = 0.0
    acquisition_costs: Dict[str, float] = field(default_factory=dict)
    acquisition_total: float = 0.0

    # Holding
    hold_months: int = 0
    holding_costs: Dict[str, float] = field(default_factory=dict)
    holding_total: float = 0.0

    # Disposition
    selling_costs: Dict[str, float] = field(default_factory=dict)
    selling_total: float = 0.0

    # Results
    total_project_cost: float = 0.0
    cash_in: float = 0.0
    net_proceeds: float = 0.0
    profit: float = 0.0
    roi_pct: float = 0.0
    margin_pct: float = 0.0
    mao: float = 0.0
    over_mao_by: float = 0.0
    breakeven_arv: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class ScreenResult:
    listing: Listing
    proforma: Optional[ProForma] = None
    verdict: str = "PASS"          # STRONG | NEGOTIATE | PASS | FILTERED
    score: float = 0.0             # 0-100, for ranking within a verdict
    reasons: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    filtered_by: Optional[str] = None
    screened_on: str = field(default_factory=lambda: date.today().isoformat())

    @property
    def is_actionable(self) -> bool:
        return self.verdict in ("STRONG", "NEGOTIATE")
