#!/usr/bin/env bash
#
# One-time setup. Creates a virtualenv, installs dependencies, installs the
# `jcscreen` command into that venv, and runs a self-check.
#
#   cd ~/Desktop/jc-deal-screener
#   ./setup.sh
#
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"

echo ""
echo "  Jersey City Deal Screener — setup"
echo "  $ROOT"
echo ""

# --- python ----------------------------------------------------------------
PY=""
for candidate in python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
done

if [ -z "$PY" ]; then
  echo "  ERROR: no python3 found."
  echo "  On macOS: brew install python@3.12   (or install from python.org)"
  exit 1
fi

VER="$($PY -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
echo "  Python $VER ($(command -v "$PY"))"

$PY -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || {
  echo "  ERROR: Python 3.9+ required, found $VER"
  exit 1
}

# --- venv ------------------------------------------------------------------
if [ ! -d .venv ]; then
  echo "  Creating virtualenv at .venv ..."
  $PY -m venv .venv
else
  echo "  Reusing existing .venv"
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "  Installing dependencies ..."
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt
python -m pip install --quiet -e .

# --- env file --------------------------------------------------------------
if [ ! -f .env ]; then
  cp .env.example .env
  echo ""
  echo "  Created .env — open it and paste your RentCast API key."
fi

mkdir -p out

# --- self-check ------------------------------------------------------------
echo ""
echo "  Running self-check ..."
jcscreen doctor || true

echo ""
echo "  Running a test scan against the sample data (no API calls) ..."
jcscreen scan --offline --explain 1 || true

cat <<'EOF'

  ------------------------------------------------------------------
  Setup done.

  Every time you open a new terminal:

      cd ~/Desktop/jc-deal-screener
      source .venv/bin/activate

  Then:

      jcscreen doctor                  check config and key
      jcscreen scan --offline          screen the sample data, no API calls
      jcscreen scan                    live scan (uses RentCast)
      jcscreen scan --explain 3        full cost breakdown for the top 3
      jcscreen comps-pool-area 07307   build a comp pool for one ZIP

  Reports land in out/ as a timestamped .csv and .xlsx.

  To schedule a daily scan:   ./scripts/install_cron.sh
  ------------------------------------------------------------------

EOF
