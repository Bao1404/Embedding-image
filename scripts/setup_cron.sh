#!/bin/bash
# =============================================================
# Cron Setup — Sequential Pipeline
# All jobs run through run_pipeline.sh to ensure:
#   1. Jobs execute one-by-one (no overlap)
#   2. RAM usage stays low
#   3. flock prevents duplicate runs
# =============================================================
APP_DIR=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$APP_DIR/logs"

# Make pipeline runner executable
chmod +x "$APP_DIR/scripts/run_pipeline.sh"

# Daily pipeline: update prices → migrate qdrant → graded prices (3:00 AM)
DAILY="0 3 * * * $APP_DIR/scripts/run_pipeline.sh daily >> $APP_DIR/logs/cron_daily.log 2>&1"

# Weekly pipeline: daily + discover + TCG + CM + forecasts (Mondays 2:00 AM)
WEEKLY="0 2 * * 1 $APP_DIR/scripts/run_pipeline.sh weekly >> $APP_DIR/logs/cron_weekly.log 2>&1"

# Full scrape: everything including full rescrape (Sundays 1:00 AM)
FULL="0 1 * * 0 $APP_DIR/scripts/run_pipeline.sh full >> $APP_DIR/logs/cron_full.log 2>&1"

# Replace old cron jobs with new pipeline jobs
(crontab -l 2>/dev/null | grep -v "update_prices.py\|migrate_to_qdrant.py\|enrich_\|generate_forecasts.py\|run_pipeline.sh"; \
 echo "$DAILY"; echo "$WEEKLY"; echo "$FULL") | crontab -

echo "Cron jobs installed:"
crontab -l | grep "run_pipeline"
echo ""
echo "Schedule:"
echo "  Daily  (3:00 AM):   update_prices → migrate_qdrant → enrich_graded"
echo "  Weekly (Mon 2:00 AM): daily + discover + enrich_tcg → enrich_cm → forecast"
echo "  Full   (Sun 1:00 AM): full scrape + all above"
echo ""
echo "All jobs run SEQUENTIALLY through run_pipeline.sh (no overlap)."
