#!/usr/bin/env bash
set -euo pipefail

HISTORY_DAYS="${1:-30}"

cd /root/betx

# One-shot refresh: scrape/backfill, grade and rescore immediately.
./.venv/bin/python -m betx --site-benchmark --history-days "$HISTORY_DAYS"

# Quick health output.
./.venv/bin/python - <<'PY'
from datetime import date
from betx.external.service import ExternalBenchmarkService
svc = ExternalBenchmarkService()
try:
    print({"top_sites": svc.get_top_sites(window_days=60, limit=5, min_graded=1)})
    print({"recommendations": len(svc.build_daily_recommendations(target_date=date.today(), top_n_sites=3, min_consensus_votes=1, window_days=60))})
finally:
    svc.close()
PY
