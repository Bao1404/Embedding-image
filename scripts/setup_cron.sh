#!/bin/bash
# =============================================================
# Cron Jobs — Full Pipeline: Scrape → MongoDB → Enrich → Forecast
# =============================================================
APP_DIR=$(cd "$(dirname "$0")/.." && pwd)
VENV="$APP_DIR/venv/bin/python3"
mkdir -p "$APP_DIR/logs"

# Weekly: Full scrape chi tiet → MongoDB (Chu nhat 2:00 AM)
WEEKLY="0 2 * * 0 cd $APP_DIR && xvfb-run -a $VENV scripts/update_prices.py --full >> logs/weekly_full.log 2>&1"

# Daily: Cap nhat gia nhanh tu list → MongoDB (3:00 AM)
DAILY="0 3 * * * cd $APP_DIR && xvfb-run -a $VENV scripts/update_prices.py --update >> logs/daily_update.log 2>&1"

# Weekly: Tim expansion moi (Thu 2 4:00 AM)
DISCOVER="0 4 * * 1 cd $APP_DIR && xvfb-run -a $VENV scripts/update_prices.py --discover >> logs/weekly_discover.log 2>&1"

# Daily: Embed anh moi → Qdrant (5:00 AM)
MIGRATE="0 5 * * * cd $APP_DIR && $VENV scripts/migrate_to_qdrant.py >> logs/daily_migrate.log 2>&1"

# Daily: Cap nhat Graded Prices tu Scrydex raw data (5:30 AM)
GRADED="30 5 * * * cd $APP_DIR && $VENV scripts/enrich_graded_prices.py >> logs/daily_graded.log 2>&1"

# Weekly: Lam giau TCGPlayer — chi the moi (Thu 2 6:00 AM)
TCG="0 6 * * 1 cd $APP_DIR && $VENV scripts/enrich_tcgplayer.py >> logs/weekly_tcg.log 2>&1"

# Weekly: Dong bo gia Cardmarket (Thu 2 6:30 AM — sau TCGPlayer)
CM="30 6 * * 1 cd $APP_DIR && $VENV scripts/enrich_cardmarket.py >> logs/weekly_cm.log 2>&1"

# Weekly: Tinh toan du bao gia (Thu 2 7:00 AM — sau tat ca enrichment)
FORECAST="0 7 * * 1 cd $APP_DIR && $VENV scripts/generate_forecasts.py >> logs/weekly_forecast.log 2>&1"

# Them vao crontab (xoa cac job cu truoc)
(crontab -l 2>/dev/null | grep -v "update_prices.py\|migrate_to_qdrant.py\|enrich_\|generate_forecasts.py"; \
 echo "$WEEKLY"; echo "$DAILY"; echo "$DISCOVER"; echo "$MIGRATE"; \
 echo "$GRADED"; echo "$TCG"; echo "$CM"; echo "$FORECAST") | crontab -

echo "Cron jobs installed:"
crontab -l | grep -E "update_prices|migrate_to_qdrant|enrich_|generate_forecasts"
