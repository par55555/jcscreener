"""Orchestration: listings in, ScreenResults out."""

from __future__ import annotations

from typing import List, Optional

from . import buybox, sources
from .comps import ArvResult, CompPool, apply_arv_policy, estimate_single, make_client
from .config import Config
from .models import Listing, ScreenResult
from .proforma import build_proforma, estimate_rehab
from .scoring import evaluate


def screen(
    listings: List[Listing],
    cfg: Config,
    *,
    offline: bool = False,
    keep_filtered: bool = False,
    verbose: bool = True,
) -> List[ScreenResult]:
    bb = cfg.buy_box
    results: List[ScreenResult] = []

    # --- Stage 1: hard filters (free) --------------------------------------
    candidates: List[Listing] = []
    for listing in listings:
        ok, why = buybox.passes(listing, bb)
        if ok:
            candidates.append(listing)
        elif keep_filtered:
            results.append(ScreenResult(
                listing=listing, verdict="FILTERED", filtered_by=why,
                reasons=[f"Filtered: {why}"],
            ))

    if verbose:
        print(f"  Buy box: {len(candidates)} of {len(listings)} listings pass hard filters")

    if not candidates:
        return results

    # --- Stage 2: ARV ------------------------------------------------------
    pool: Optional[CompPool] = None
    client = None

    if not offline:
        use_pool = bool(cfg.comps.get("use_pool", True))
        client = make_client(cfg)
        if use_pool:
            zips = sorted({l.zipcode for l in candidates if l.zipcode})
            pool = CompPool(client, cfg).load(zips)
            if verbose:
                for z in zips:
                    print(f"  Comp pool {z}: {pool.pool_size(z)} recent sales "
                          f"({client.call_count} API call(s) so far)")

    # --- Stage 3: pro-forma + verdict --------------------------------------
    for listing in candidates:
        arv_result = _arv_for(listing, cfg, pool, client, offline)

        if not arv_result or arv_result.arv <= 0:
            results.append(ScreenResult(
                listing=listing, verdict="PASS",
                reasons=["No usable ARV — " + (arv_result.notes[0] if arv_result and arv_result.notes
                                               else "no comps available")],
                risks=buybox.find_risks(listing, bb),
            ))
            continue

        underwrite_arv, arv_note = apply_arv_policy(arv_result, cfg)

        rehab = estimate_rehab(listing, cfg)
        pf = build_proforma(
            listing, underwrite_arv, cfg,
            arv_low=arv_result.low, arv_high=arv_result.high,
            arv_confidence=arv_result.confidence, rehab=rehab,
        )

        verdict, score, reasons = evaluate(pf, cfg)
        reasons.insert(0, arv_note + f" ({arv_result.method}, {arv_result.comp_count} comps, "
                                     f"{arv_result.confidence} confidence)")

        risks = buybox.find_risks(listing, bb)
        if arv_result.confidence == "low":
            risks.append("Low-confidence ARV — verify with a broker BPO before offering")

        results.append(ScreenResult(
            listing=listing, proforma=pf, verdict=verdict,
            score=score, reasons=reasons, risks=risks,
        ))

    if client and verbose:
        print(f"  RentCast API calls used: {client.call_count}")

    order = {"STRONG": 0, "NEGOTIATE": 1, "PASS": 2, "FILTERED": 3}
    results.sort(key=lambda r: (order.get(r.verdict, 9), -r.score))
    return results


def _arv_for(listing, cfg, pool, client, offline) -> Optional[ArvResult]:
    # An arv_hint column on the listing always wins — that is how you inject a
    # broker's opinion or your own number and override the model.
    hint = getattr(listing, "arv_hint", None)
    if hint:
        return ArvResult(arv=float(hint), low=float(hint) * 0.95, high=float(hint) * 1.05,
                         confidence="high", method="hint", comp_count=0,
                         notes=["ARV supplied on the listing"])

    if offline:
        return None

    if pool is not None:
        res = pool.estimate(listing)
        # Fall back to a per-property AVM when the pool cannot value it.
        if res.arv <= 0 and client is not None:
            try:
                return estimate_single(listing, cfg, client)
            except Exception:
                return res
        return res

    try:
        return estimate_single(listing, cfg, client)
    except Exception as exc:
        print(f"  ! AVM failed for {listing.address}: {exc}")
        return None


def run(
    cfg: Config,
    source: Optional[str] = None,
    *,
    offline: bool = False,
    keep_filtered: bool = False,
    verbose: bool = True,
    **source_kwargs,
) -> List[ScreenResult]:
    src = source or cfg.sources.get("default", "csv")
    if verbose:
        print(f"  Source: {src}")
    listings = sources.load(src, cfg, **source_kwargs)
    if verbose:
        print(f"  Loaded {len(listings)} listing(s)")
    return screen(listings, cfg, offline=offline, keep_filtered=keep_filtered, verbose=verbose)
