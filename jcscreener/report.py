"""
Output: terminal summary, timestamped CSV, and a formatted Excel workbook.

The workbook has three sheets:
  Deals     — one row per screened property, colour-coded by verdict
  Pro-forma — the full itemized cost stack for actionable deals
  Config    — the exact assumptions that produced these numbers, so a file
              you saved three months ago is still interpretable
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from .config import Config
from .models import ScreenResult

# --- Colour-blind-safe verdict palette (works in light and dark) -----------
VERDICT_FILL = {
    "STRONG": "C7E9C0",      # green
    "NEGOTIATE": "FDD9A0",   # amber
    "PASS": "E6E6E6",        # grey
    "FILTERED": "F2F2F2",
}
VERDICT_FONT = {
    "STRONG": "1B5E20",
    "NEGOTIATE": "7A4A00",
    "PASS": "555555",
    "FILTERED": "888888",
}

CSV_COLUMNS = [
    "verdict", "score", "address", "zipcode", "neighborhood", "units",
    "beds", "baths", "sqft", "year_built", "days_on_market",
    "price", "price_per_sqft", "arv", "arv_confidence",
    "rehab_total", "rehab_tier",
    "acquisition_total", "holding_total", "selling_total",
    "total_project_cost", "cash_in", "profit", "roi_pct", "margin_pct",
    "mao", "over_mao_by", "breakeven_arv",
    "risks", "reasons", "url",
]


def _row(r: ScreenResult) -> Dict[str, Any]:
    l, pf = r.listing, r.proforma
    row = {
        "verdict": r.verdict,
        "score": r.score,
        "address": l.full_address,
        "zipcode": l.zipcode,
        "neighborhood": l.neighborhood,
        "units": l.units,
        "beds": l.beds,
        "baths": l.baths,
        "sqft": l.sqft,
        "year_built": l.year_built,
        "days_on_market": l.days_on_market,
        "price": l.price,
        "price_per_sqft": l.price_per_sqft,
        "risks": "; ".join(r.risks),
        "reasons": " | ".join(r.reasons),
        "url": l.url,
    }
    if pf:
        row.update({
            "arv": pf.arv,
            "arv_confidence": pf.arv_confidence,
            "rehab_total": pf.rehab.total if pf.rehab else None,
            "rehab_tier": pf.rehab.tier if pf.rehab else None,
            "acquisition_total": pf.acquisition_total,
            "holding_total": pf.holding_total,
            "selling_total": pf.selling_total,
            "total_project_cost": pf.total_project_cost,
            "cash_in": pf.cash_in,
            "profit": pf.profit,
            "roi_pct": pf.roi_pct,
            "margin_pct": pf.margin_pct,
            "mao": pf.mao,
            "over_mao_by": pf.over_mao_by,
            "breakeven_arv": pf.breakeven_arv,
        })
    return row


# ---------------------------------------------------------------------------
# Terminal
# ---------------------------------------------------------------------------

def print_summary(results: List[ScreenResult], alerts=None) -> None:
    counts: Dict[str, int] = {}
    for r in results:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1

    print()
    print("=" * 78)
    print("  SCREEN RESULTS")
    print("=" * 78)
    for v in ("STRONG", "NEGOTIATE", "PASS", "FILTERED"):
        if counts.get(v):
            print(f"  {v:<10} {counts[v]:>4}")
    print("-" * 78)

    actionable = [r for r in results if r.is_actionable]
    if not actionable:
        print("  Nothing actionable this run.")
    for r in actionable[:25]:
        pf = r.proforma
        print()
        print(f"  [{r.verdict}]  score {r.score:>5.1f}   {r.listing.full_address}")
        if pf:
            line = f"      Ask ${pf.purchase_price:>10,.0f}   ARV ${pf.arv:>10,.0f}"
            if pf.rehab:
                line += f"   Rehab ${pf.rehab.total:>9,.0f}"
            print(line)
            print(f"      Profit ${pf.profit:>9,.0f}   ROI {pf.roi_pct:>6.1f}%   "
                  f"Margin {pf.margin_pct:>5.1f}%   MAO ${pf.mao:,.0f}")
        for reason in r.reasons[:3]:
            print(f"      - {reason}")
        if r.risks:
            print(f"      ! {'; '.join(r.risks[:3])}")

    if alerts:
        print()
        print("-" * 78)
        print(f"  NEW ALERTS ({len(alerts)})")
        for r, why in alerts:
            print(f"    * {r.listing.full_address} — {why}")
    print("=" * 78)
    print()


def print_explain(r: ScreenResult) -> None:
    """Full HUD-style breakdown for one deal."""
    pf = r.proforma
    if not pf:
        print(f"No pro-forma for {r.listing.full_address} ({r.filtered_by or 'no ARV'})")
        return

    w = 62
    print()
    print("=" * w)
    print(f"  {r.listing.full_address}")
    print(f"  {r.verdict}  (score {r.score})")
    print("=" * w)

    def block(title: str, items: Dict[str, float], total: float):
        print(f"\n  {title}")
        for k, v in items.items():
            print(f"    {k.replace('_', ' '):<34} {v:>14,.2f}")
        print(f"    {'':<34} {'-' * 14}")
        print(f"    {'subtotal':<34} {total:>14,.2f}")

    print(f"\n  {'Purchase price':<34} {pf.purchase_price:>14,.2f}")
    print(f"  {'ARV used for underwriting':<34} {pf.arv:>14,.2f}")
    if pf.arv_low and pf.arv_high:
        print(f"  {'  raw comp range':<34} {pf.arv_low:>7,.0f} - {pf.arv_high:,.0f}"
              f"   ({pf.arv_confidence} confidence)")

    if pf.rehab:
        rh = pf.rehab
        print(f"\n  Rehab — {rh.tier} @ ${rh.base_psf:.0f}/sqft")
        print(f"    {'base':<34} {rh.base_cost:>14,.2f}")
        if rh.unit_adders:
            print(f"    {'extra unit adders':<34} {rh.unit_adders:>14,.2f}")
        for k, v in rh.keyword_adders.items():
            print(f"    {k:<34} {v:>14,.2f}")
        print(f"    {'contingency':<34} {rh.contingency:>14,.2f}")
        print(f"    {'':<34} {'-' * 14}")
        print(f"    {'subtotal':<34} {rh.total:>14,.2f}")

    block("Acquisition", pf.acquisition_costs, pf.acquisition_total)
    block(f"Holding ({pf.hold_months} months)", pf.holding_costs, pf.holding_total)
    block("Disposition", pf.selling_costs, pf.selling_total)

    print(f"\n  {'=' * (w - 2)}")
    print(f"  {'Total project cost':<34} {pf.total_project_cost:>14,.2f}")
    print(f"  {'Cash in (your money)':<34} {pf.cash_in:>14,.2f}")
    print(f"  {'Net sale proceeds':<34} {pf.net_proceeds:>14,.2f}")
    print(f"  {'PROFIT':<34} {pf.profit:>14,.2f}")
    print(f"  {'ROI':<34} {pf.roi_pct:>13,.1f}%")
    print(f"  {'Margin on ARV':<34} {pf.margin_pct:>13,.1f}%")
    print(f"  {'Max allowable offer':<34} {pf.mao:>14,.2f}")
    print(f"  {'Breakeven ARV':<34} {pf.breakeven_arv:>14,.2f}")

    print("\n  Why:")
    for reason in r.reasons:
        print(f"    - {reason}")
    if r.risks:
        print("\n  Risks:")
        for risk in r.risks:
            print(f"    ! {risk}")
    print()


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def write_csv(results: List[ScreenResult], cfg: Config, stamp: str) -> Path:
    out_dir = cfg.resolve(cfg.output.get("dir", "out"))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"scan_{stamp}.csv"

    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for r in results:
            writer.writerow(_row(r))
    return path


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------

def write_xlsx(results: List[ScreenResult], cfg: Config, stamp: str) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    out_dir = cfg.resolve(cfg.output.get("dir", "out"))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"scan_{stamp}.xlsx"

    wb = Workbook()

    # ---------------- Sheet 1: Deals ----------------
    ws = wb.active
    ws.title = "Deals"

    header_fill = PatternFill("solid", fgColor="1F3A5F")
    header_font = Font(color="FFFFFF", bold=True, size=10)
    thin = Side(style="thin", color="D0D0D0")
    border = Border(bottom=thin)

    money = {"price", "arv", "rehab_total", "acquisition_total", "holding_total",
             "selling_total", "total_project_cost", "cash_in", "profit", "mao",
             "over_mao_by", "breakeven_arv", "price_per_sqft"}
    pct = {"roi_pct", "margin_pct"}

    display = [c for c in CSV_COLUMNS if c != "reasons"]

    ws.append([c.replace("_", " ").title() for c in display])
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "D2"

    for r in results:
        row = _row(r)
        ws.append([row.get(c) for c in display])
        i = ws.max_row
        fill = PatternFill("solid", fgColor=VERDICT_FILL.get(r.verdict, "FFFFFF"))
        font = Font(color=VERDICT_FONT.get(r.verdict, "000000"), bold=(r.verdict == "STRONG"))
        ws.cell(i, 1).fill = fill
        ws.cell(i, 1).font = font
        for j, col in enumerate(display, start=1):
            c = ws.cell(i, j)
            c.border = border
            if col in money:
                c.number_format = '$#,##0'
            elif col in pct:
                c.number_format = '0.0"%"'
            elif col == "score":
                c.number_format = '0.0'

    widths = {"address": 42, "reasons": 60, "risks": 46, "neighborhood": 18,
              "verdict": 12, "url": 30}
    for j, col in enumerate(display, start=1):
        ws.column_dimensions[get_column_letter(j)].width = widths.get(col, 13)

    ws.auto_filter.ref = f"A1:{get_column_letter(len(display))}{ws.max_row}"

    # ---------------- Sheet 2: Pro-forma ----------------
    ws2 = wb.create_sheet("Pro-forma")
    ws2.append(["Full itemized cost stack — actionable deals only"])
    ws2["A1"].font = Font(bold=True, size=12)
    ws2.append([])

    label_font = Font(bold=True)
    for r in [x for x in results if x.is_actionable]:
        pf = r.proforma
        if not pf:
            continue
        ws2.append([r.listing.full_address, "", r.verdict, f"score {r.score}"])
        ws2.cell(ws2.max_row, 1).font = Font(bold=True, size=11)
        ws2.cell(ws2.max_row, 1).fill = PatternFill(
            "solid", fgColor=VERDICT_FILL.get(r.verdict, "FFFFFF"))

        def line(label, value, bold=False, money_fmt=True):
            ws2.append(["", label, value])
            c = ws2.cell(ws2.max_row, 3)
            c.number_format = '$#,##0' if money_fmt else '0.0"%"'
            if bold:
                ws2.cell(ws2.max_row, 2).font = label_font
                c.font = label_font

        line("Purchase price", pf.purchase_price, bold=True)
        line("ARV", pf.arv, bold=True)
        if pf.rehab:
            line("Rehab — base", pf.rehab.base_cost)
            if pf.rehab.unit_adders:
                line("Rehab — unit adders", pf.rehab.unit_adders)
            for k, v in pf.rehab.keyword_adders.items():
                line(f"Rehab — {k}", v)
            line("Rehab — contingency", pf.rehab.contingency)
            line("Rehab total", pf.rehab.total, bold=True)
        for k, v in pf.acquisition_costs.items():
            line(f"Acq — {k.replace('_', ' ')}", v)
        line("Acquisition total", pf.acquisition_total, bold=True)
        for k, v in pf.holding_costs.items():
            line(f"Hold — {k.replace('_', ' ')}", v)
        line(f"Holding total ({pf.hold_months} mo)", pf.holding_total, bold=True)
        for k, v in pf.selling_costs.items():
            line(f"Sell — {k.replace('_', ' ')}", v)
        line("Disposition total", pf.selling_total, bold=True)
        line("Cash in", pf.cash_in, bold=True)
        line("PROFIT", pf.profit, bold=True)
        line("ROI", pf.roi_pct, bold=True, money_fmt=False)
        line("Max allowable offer", pf.mao, bold=True)
        ws2.append([])

    ws2.column_dimensions["A"].width = 4
    ws2.column_dimensions["B"].width = 36
    ws2.column_dimensions["C"].width = 16
    ws2.column_dimensions["D"].width = 14

    # ---------------- Sheet 3: Config ----------------
    ws3 = wb.create_sheet("Assumptions")
    ws3.append(["These are the exact assumptions behind the numbers in this file."])
    ws3["A1"].font = Font(bold=True, size=12)
    ws3.append([f"Generated {datetime.now():%Y-%m-%d %H:%M}"])
    ws3.append([])

    def dump(d: Dict[str, Any], prefix: str = ""):
        for k, v in d.items():
            if isinstance(v, dict):
                dump(v, f"{prefix}{k}.")
            elif isinstance(v, list):
                ws3.append([f"{prefix}{k}", ", ".join(str(x) for x in v)])
            else:
                ws3.append([f"{prefix}{k}", v])

    for section in ("buy_box", "underwriting", "rehab", "costs", "comps"):
        ws3.append([section.upper()])
        ws3.cell(ws3.max_row, 1).font = Font(bold=True)
        dump(cfg.raw.get(section, {}))
        ws3.append([])

    ws3.column_dimensions["A"].width = 44
    ws3.column_dimensions["B"].width = 40

    wb.save(path)
    return path


def write_all(results: List[ScreenResult], cfg: Config) -> Dict[str, Path]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    written: Dict[str, Path] = {}
    if cfg.output.get("write_csv", True):
        written["csv"] = write_csv(results, cfg, stamp)
    if cfg.output.get("write_xlsx", True):
        try:
            written["xlsx"] = write_xlsx(results, cfg, stamp)
        except ImportError:
            print("  ! openpyxl not installed — skipping Excel output "
                  "(pip install openpyxl)")
    return written
