"""Command line interface.

    jcscreen scan                       # screen the default source
    jcscreen scan --source rentcast     # pull live listings, then screen
    jcscreen scan --offline             # no API calls; uses arv_hint column
    jcscreen scan --explain 1           # full HUD breakdown for the top deal

    jcscreen comps "123 Main St, Jersey City, NJ 07307" --sqft 2400 --beds 6
    jcscreen comps-pool-area 07307 07306
    jcscreen comps-pool-csv data/my_listings.csv

    jcscreen doctor                     # check config, key, deps
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

from . import report, screener, sources
from .alerts import select_alerts
from .comps import CompPool, apply_arv_policy, estimate_single, make_client
from .config import ConfigError, load_config
from .models import Listing


def _load_env(root: Path) -> None:
    """Minimal .env reader so there is no hard dependency on python-dotenv."""
    env = root / ".env"
    if not env.exists():
        return
    import os
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_scan(args, cfg) -> int:
    print(f"\n  Buy box: {cfg.buy_box.name}")

    kwargs = {}
    if args.path:
        kwargs["path"] = args.path

    results = screener.run(
        cfg, source=args.source, offline=args.offline,
        keep_filtered=args.keep_filtered, **kwargs,
    )

    alerts, state = select_alerts(results, cfg)
    if not args.no_state:
        state.save()

    report.print_summary(results, alerts)

    if args.explain:
        actionable = [r for r in results if r.is_actionable]
        for r in actionable[: args.explain]:
            report.print_explain(r)

    written = report.write_all(results, cfg)
    for kind, path in written.items():
        print(f"  {kind.upper():<5} -> {path}")
    print()

    # Exit 0 normally; 10 when there are new STRONG alerts, so cron/scripts can
    # branch on it (e.g. only send a notification when something is new).
    return 10 if alerts else 0


def cmd_comps(args, cfg) -> int:
    parts = [p.strip() for p in args.address.split(",")]
    zipcode = ""
    if parts and parts[-1]:
        tail = parts[-1].split()
        if tail and tail[-1].isdigit():
            zipcode = tail[-1]

    listing = Listing(
        address=parts[0], zipcode=zipcode,
        sqft=args.sqft, beds=args.beds, baths=args.baths, units=args.units,
        price=args.price or 0,
    )

    result = estimate_single(listing, cfg)
    final, note = apply_arv_policy(result, cfg)

    print(f"\n  {args.address}")
    print(f"  AVM              ${result.arv:>12,.0f}")
    if result.low and result.high:
        print(f"  Range            ${result.low:>12,.0f}  -  ${result.high:,.0f}")
    print(f"  Confidence       {result.confidence:>13}")
    print(f"  Comparables      {result.comp_count:>13}")
    print(f"  Underwriting ARV ${final:>12,.0f}   ({note})")
    for n in result.notes:
        print(f"    - {n}")
    print()
    return 0


def cmd_comps_pool_area(args, cfg) -> int:
    client = make_client(cfg)
    zips = args.zips or cfg.buy_box.zips
    pool = CompPool(client, cfg).load(zips)

    print()
    for z in zips:
        print(f"  {z}: {pool.pool_size(z)} recent sales in the pool")
    print(f"\n  Total RentCast API calls: {client.call_count} "
          f"(a per-property AVM would have cost one call per listing)\n")
    return 0


def cmd_comps_pool_csv(args, cfg) -> int:
    listings: List[Listing] = sources.load("csv", cfg, path=args.path)
    print(f"\n  {len(listings)} listing(s) from {args.path}")

    client = make_client(cfg)
    zips = sorted({l.zipcode for l in listings if l.zipcode})
    pool = CompPool(client, cfg).load(zips)

    print(f"  Pooled {len(zips)} area(s) in {client.call_count} API call(s)\n")
    print(f"  {'ADDRESS':<44} {'ARV':>12} {'CONF':>8} {'COMPS':>6}")
    print("  " + "-" * 74)

    for l in listings:
        res = pool.estimate(l)
        final, _ = apply_arv_policy(res, cfg) if res.arv else (0.0, "")
        print(f"  {l.address[:43]:<44} {final:>12,.0f} {res.confidence:>8} {res.comp_count:>6}")

    print(f"\n  Done. {client.call_count} API call(s) for {len(listings)} properties.\n")
    return 0


def cmd_doctor(args, cfg) -> int:
    import os
    ok = True
    print("\n  Config")
    print(f"    file                {cfg.path}")
    print(f"    buy box             {cfg.buy_box.name}")
    print(f"    zips                {', '.join(cfg.buy_box.zips) or '(none)'}")
    print(f"    price band          ${cfg.buy_box.price_min:,.0f} - ${cfg.buy_box.price_max:,.0f}")
    print(f"    hold months         {cfg.underwriting.hold_months}")
    print(f"    MAO factor          {cfg.underwriting.mao_arv_factor}")

    print("\n  Credentials")
    key = os.environ.get("RENTCAST_API_KEY", "")
    if key:
        print(f"    RENTCAST_API_KEY    set (...{key[-4:]})")
    else:
        print("    RENTCAST_API_KEY    MISSING — copy .env.example to .env")
        ok = False

    print("\n  Dependencies")
    for mod in ("yaml", "requests", "openpyxl"):
        try:
            __import__(mod)
            print(f"    {mod:<19} ok")
        except ImportError:
            print(f"    {mod:<19} MISSING")
            ok = False

    print("\n  Data")
    src = cfg.sources.get("default", "csv")
    print(f"    default source      {src}")
    if src in ("csv", "json"):
        p = cfg.resolve(cfg.sources.get(src, {}).get("path", ""))
        print(f"    path                {p} {'ok' if Path(p).exists() else 'MISSING'}")
        if not Path(p).exists():
            ok = False

    print(f"\n  {'All good.' if ok else 'Fix the items above before running a live scan.'}\n")
    return 0 if ok else 1


# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jcscreen",
        description="Jersey City flip deal screener.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--config", help="path to screener.yaml")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scan", help="screen listings and write reports")
    s.add_argument("--source", help="csv | json | rentcast")
    s.add_argument("--path", help="override the source file path")
    s.add_argument("--offline", action="store_true", help="no API calls")
    s.add_argument("--keep-filtered", action="store_true",
                   help="include buy-box rejects in the report")
    s.add_argument("--explain", type=int, nargs="?", const=1, default=0,
                   metavar="N", help="print a full breakdown for the top N deals")
    s.add_argument("--no-state", action="store_true",
                   help="do not update the dedup state file")
    s.set_defaults(func=cmd_scan)

    c = sub.add_parser("comps", help="AVM for a single address (1 API call)")
    c.add_argument("address")
    c.add_argument("--sqft", type=float)
    c.add_argument("--beds", type=float)
    c.add_argument("--baths", type=float)
    c.add_argument("--units", type=int, default=1)
    c.add_argument("--price", type=float)
    c.set_defaults(func=cmd_comps)

    a = sub.add_parser("comps-pool-area", help="build comp pools for ZIPs (1 call each)")
    a.add_argument("zips", nargs="*")
    a.set_defaults(func=cmd_comps_pool_area)

    b = sub.add_parser("comps-pool-csv", help="batch-value a CSV of properties")
    b.add_argument("path")
    b.set_defaults(func=cmd_comps_pool_csv)

    d = sub.add_parser("doctor", help="check config, key and dependencies")
    d.set_defaults(func=cmd_doctor)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"\n  {exc}\n", file=sys.stderr)
        return 2

    _load_env(cfg.root)

    try:
        return args.func(args, cfg)
    except KeyboardInterrupt:
        print("\n  Interrupted.\n")
        return 130
    except Exception as exc:
        print(f"\n  Error: {exc}\n", file=sys.stderr)
        if "--debug" in sys.argv:
            raise
        return 1


if __name__ == "__main__":
    sys.exit(main())
