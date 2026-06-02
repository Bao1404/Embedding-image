#!/bin/bash
# =============================================================
# Sequential Pipeline Runner
# Runs all enrichment jobs one-by-one, ensuring no overlap.
# Cron only calls THIS script — never individual jobs directly.
# =============================================================

APP_DIR=$(cd "$(dirname "$0")/.." && pwd)
VENV="$APP_DIR/venv/bin/python3"
LOCKFILE="/tmp/pokemon_pipeline.lock"
LOG_DIR="$APP_DIR/logs"

mkdir -p "$LOG_DIR"

# Use flock to prevent overlapping runs (e.g. if pipeline takes >24h)
exec 200>"$LOCKFILE"
if ! flock -n 200; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') [SKIP] Pipeline already running. Exiting." >> "$LOG_DIR/pipeline.log"
    exit 0
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') [START] Pipeline started." >> "$LOG_DIR/pipeline.log"

# Determine run mode from arguments
# Usage:
#   ./run_pipeline.sh daily       # Daily jobs only
#   ./run_pipeline.sh weekly      # Weekly jobs (includes daily + enrichment + forecast)
#   ./run_pipeline.sh full        # Full scrape + all jobs
MODE="${1:-daily}"

run_step() {
    local step_name="$1"
    shift
    echo "$(date '+%Y-%m-%d %H:%M:%S') [RUN] $step_name" >> "$LOG_DIR/pipeline.log"
    "$@" >> "$LOG_DIR/pipeline_${step_name}.log" 2>&1
    local exit_code=$?
    if [ $exit_code -ne 0 ]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') [ERROR] $step_name failed with exit code $exit_code" >> "$LOG_DIR/pipeline.log"
    else
        echo "$(date '+%Y-%m-%d %H:%M:%S') [DONE] $step_name" >> "$LOG_DIR/pipeline.log"
    fi
    return $exit_code
}

cd "$APP_DIR"

# ─── FULL MODE: Full scrape (only on explicit request) ───
if [ "$MODE" = "full" ]; then
    run_step "scrape_full" xvfb-run -a "$VENV" scripts/update_prices.py --full
fi

# ─── DAILY JOBS ───
# Step 1: Update prices (quick daily sync)
run_step "update_prices" xvfb-run -a "$VENV" scripts/update_prices.py --update

# Step 2: Migrate new images to Qdrant
run_step "migrate_qdrant" "$VENV" scripts/migrate_to_qdrant.py

# Step 3: Enrich graded prices
run_step "enrich_graded" "$VENV" scripts/enrich_graded_prices.py

# ─── WEEKLY JOBS (only on "weekly" or "full" mode) ───
if [ "$MODE" = "weekly" ] || [ "$MODE" = "full" ]; then
    # Step 4: Discover new expansions
    run_step "discover" xvfb-run -a "$VENV" scripts/update_prices.py --discover

    # Step 5: Enrich TCGPlayer prices (needs network, slow)
    run_step "enrich_tcg" "$VENV" scripts/enrich_tcgplayer.py

    # Step 6: Map Cardmarket prices from TCG data
    run_step "enrich_cm" "$VENV" scripts/enrich_cardmarket.py

    # Step 7: Generate forecasts (must run AFTER all enrichment)
    run_step "forecast" "$VENV" scripts/generate_forecasts.py
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') [FINISH] Pipeline completed ($MODE mode)." >> "$LOG_DIR/pipeline.log"
