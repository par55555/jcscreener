# Jersey City Deal Screener

Screens Jersey City multifamily listings against your buy box, runs a full flip
pro-forma with NJ/Hudson County/JC-specific costs, and returns a
**STRONG / NEGOTIATE / PASS** verdict with the exact number you should offer.

---

## Setup — 60 seconds

```bash
cd ~/Desktop/jc-deal-screener
./setup.sh
```

That creates a virtualenv, installs everything, installs the `jcscreen`
command, and runs a test scan against the sample data.

Then put your RentCast key in `.env`:

```bash
open .env          # paste your key after RENTCAST_API_KEY=
```

Get a key at https://app.rentcast.io/app/api. **If a key of yours has ever been
pasted into a chat, an email, or a commit, rotate it before using it here.**

Every new terminal session:

```bash
cd ~/Desktop/jc-deal-screener
source .venv/bin/activate
```

---

## Daily use

```bash
jcscreen doctor                     # config, key, deps — run this if anything looks off
jcscreen scan --offline             # screen sample data, zero API calls
jcscreen scan                       # live scan
jcscreen scan --explain 3           # full HUD-style breakdown for the top 3
jcscreen scan --keep-filtered       # also show what the buy box rejected, and why
jcscreen scan --source rentcast     # pull live listings from RentCast, then screen
```

Reports land in `out/` as a timestamped `.csv` and a formatted `.xlsx`.

### Comps on their own

```bash
jcscreen comps "148 Ogden Ave, Jersey City, NJ 07307" --sqft 1800 --beds 6 --units 2
jcscreen comps-pool-area 07307 07306
jcscreen comps-pool-csv data/my_listings.csv
```

---

## The three CLI modes for comps, and why they differ

| Mode | API calls | Use it when |
|---|---|---|
| `comps` | 1 per property | You are looking hard at one specific address. Most accurate — uses RentCast's own AVM. |
| `comps-pool-area` | 1 per ZIP | You want to warm the cache before a sweep, or just see how thin a market is. |
| `comps-pool-csv` | 1 per ZIP, total | You have a list of 50 properties. This values all of them off one pulled comp set per area. |

`CompPool` is the whole point: a 200-listing sweep across four ZIPs costs
**4 API calls, not 200**. `use_pool: true` in the config turns it on for
`jcscreen scan` as well. Responses are cached on disk for 24h, so re-running a
scan the same day is free.

---

## Scheduled daily scan

```bash
./scripts/install_cron.sh            # 07:15 every day
./scripts/install_cron.sh 6 30       # 06:30 instead
./scripts/install_cron.sh --remove
```

It prints a `launchd` plist equivalent too, if you prefer that on macOS.

The runner is dedup-aware: **a deal is alerted once.** It re-alerts only when
the price moves more than `realert_on_price_change_pct` (default 2.5%) or the
verdict improves. Without that you get the same eight properties every morning
until you stop reading them.

`jcscreen scan` exits **10** when there are new alerts and **0** when the scan
was clean, so the runner stays quiet on uneventful days and fires a macOS
notification only when something is actually new. There is a commented-out
`mail` line in `scripts/run_scan.sh` if you would rather get an email.

State lives in `out/.seen_deals.json` — plain JSON. Delete a line to force a
re-alert.

---

## Tuning it

Everything lives in **`config/screener.yaml`**. You should not need to touch
Python to change strategy.

The knobs that matter most, in order:

1. **`rehab.tiers`** — dollars per square foot. This is the single biggest
   source of error in flip underwriting. Every time you get a real contractor
   bid, update these. Nothing else improves accuracy as much.
2. **`underwriting.mao_arv_factor`** — 0.70 is the textbook flip rule; the
   default here is 0.75 because 70% deals essentially do not exist on the JC
   MLS. Lower it when you have more deal flow than capital.
3. **`underwriting.hold_months`** — be honest. JC permit timelines are slow;
   7 months is realistic and 6 is optimistic for anything structural.
4. **`underwriting.arv_mode`** — defaults to `conservative`, which underwrites
   off the low end of the comp range and then takes another 3% off. Underwriting
   off the optimistic end is how people lose money on flips.
5. **`costs.financing.mode`** — `owner_occupied` models the house-hack entry;
   switch to `hard_money` to see what the same deal looks like on a straight flip.

---

## How a verdict is reached

```
buy box filter  →  ARV  →  rehab estimate  →  pro-forma  →  thresholds  →  verdict
   (free)          (API)     (config)         (config)      (config)
```

**STRONG** — clears every underwriting threshold at the asking price. Rare on
the MLS. Act same-day.

**NEGOTIATE** — fails at asking, but the gap to your max allowable offer is
inside the negotiating band. The report tells you where to open and where to
walk.

**PASS** — the math does not work at a price you would realistically get.

`--explain` prints the entire cost stack line by line, so you can see exactly
which assumption killed a deal rather than trusting a verdict.

---

## The New Jersey specifics baked in

These are the things a generic national flip calculator gets wrong:

- **Realty Transfer Fee** — the bracketed per-$500 schedule, both the
  at-or-below-$350k table and the above-$350k table, transcribed from the NJ
  Division of Taxation. The schedules are not continuous: a dollar over $350k
  costs meaningfully more fee, which is why sellers price around it.
- **Graduated Percent Fee ("mansion tax")** — as of the 2025 statutory change
  the **seller** owes it, and it is a flat percentage of the *whole*
  consideration, stepping 1% → 3.5%. The cliff at $1,000,000 is about $10,000,
  which is why JC listings cluster at $999,000. On a flip this lands on your
  exit HUD, not your acquisition.
- **Jersey City property tax** — computed from the certified general tax rate
  (2.335 per $100 of *assessed* value) bridged to market value by the county
  equalization ratio (72.82%). Roughly 1.70% of market value. Abatements
  distort this badly — pull the actual tax card before you make an offer.
- **Attorney review** — NJ is an attorney-review state; buyer and seller
  attorney fees are line items on both ends, not optional.
- **Anti-Eviction Act / rent leveling** — occupied buildings and 3+ unit
  properties get flagged as risks rather than silently underwritten. `tenant
  occupied` is a hard exclusion in the default buy box for the owner-occupied
  entry strategy.
- **Pre-1978 / pre-1940 stock** — automatic lead-paint and knob-and-tube flags.
  Most of the Heights and Journal Square inventory trips both.

---

## Adding your own listing source

The screener does not care where listings come from. CSV, JSON and RentCast
ship in the box; add anything else in `jcscreener/sources.py`:

```python
@register("my_mls_export")
def load_my_mls(cfg, **kwargs):
    rows = ...                       # whatever you have
    return [l for l in (normalize(r, source="my_mls_export") for r in rows) if l]
```

Then `jcscreen scan --source my_mls_export`.

`normalize()` tolerates messy column names — `List Price`, `Bedrooms`,
`SquareFootage`, `Zip`, `MLS #` and friends all map correctly, and it strips
`$` and `,` out of numbers.

### Overriding the model per property

Two columns on any listing beat the defaults:

- **`arv_hint`** — a broker's BPO, an appraisal, or your own number. Skips comps
  entirely for that property and always wins.
- **`rehab_tier`** — `cosmetic` / `moderate` / `heavy` / `gut` for that one
  property, instead of the config default.

`data/sample_listings.csv` uses both, which is why `--offline` works with no
API key at all.

---

## Layout

```
jc-deal-screener/
├── setup.sh                  one-time installer
├── config/screener.yaml      ← everything you tune lives here
├── .env                      RentCast key (gitignored)
├── jcscreener/
│   ├── njfees.py             NJ transfer tax + JC property tax math
│   ├── proforma.py           the flip pro-forma
│   ├── comps.py              RentCast client, CompPool, compute_arv()
│   ├── buybox.py             hard filters + risk flags
│   ├── scoring.py            STRONG / NEGOTIATE / PASS
│   ├── sources.py            source-agnostic listing intake
│   ├── alerts.py             dedup state
│   ├── report.py             terminal, CSV, Excel
│   └── screener.py           orchestration
├── scripts/
│   ├── run_scan.sh           what cron calls
│   └── install_cron.sh       schedule installer
├── data/sample_listings.csv  16 seeded JC properties, no API key needed
├── tests/                    54 tests
└── out/                      reports land here
```

Run the tests any time you change the math:

```bash
pytest -q
```

---

## Two honest caveats

**The rehab number is the weak link.** Everything else in this tool is
arithmetic on published rates. The `$/sqft` tiers are placeholders until you
replace them with your own contractor's actuals. A screener with wrong rehab
numbers will confidently recommend bad deals.

**Nothing here is a substitute for the attorney.** The buy box flags rent
leveling and Anti-Eviction Act exposure; it does not evaluate them. NJ is an
attorney-review state for a reason.

---

## Sources

- [NJ Division of Taxation — Realty Transfer Fee](https://www.nj.gov/treasury/taxation/realty.shtml)
- [NJ Division of Taxation — Abstract of Ratables, Hudson County](https://www.nj.gov/njbonds/treasury/taxation/pdf/lpt/ratables/Hudson2025.pdf)
- [Jersey City 2026 equalization ratio](https://civicparent.org/2026/03/25/2026-property-tax-appeals-a-primer-for-jersey-city-as-ratio-sinks-to-72-82/)
