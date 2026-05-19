#!/bin/bash
# Setup cron jobs cho automated price updates & embedding migration
APP_DIR=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$APP_DIR/logs"

# Daily: Price-Only update (3:00 AM)
DAILY="0 3 * * * cd $APP_DIR && xvfb-run -a python3 scripts/update_prices.py --update >> logs/daily_update.log 2>&1"

# Weekly: Full scrape (Chủ nhật 2:00 AM)
WEEKLY="0 2 * * 0 cd $APP_DIR && xvfb-run -a python3 scripts/update_prices.py --full >> logs/weekly_full.log 2>&1"

# Weekly: Discover (Thứ 2 4:00 AM)
DISCOVER="0 4 * * 1 cd $APP_DIR && xvfb-run -a python3 scripts/update_prices.py --discover >> logs/weekly_discover.log 2>&1"

# Daily: Embed & push new cards to Qdrant (5:00 AM — sau khi update/discover xong)
# Tự skip thẻ đã có, tự dừng khi hết RPD limit, hôm sau chạy tiếp
MIGRATE="0 5 * * * cd $APP_DIR && python3 scripts/migrate_to_qdrant.py >> logs/daily_migrate.log 2>&1"

# Thêm vào crontab
(crontab -l 2>/dev/null | grep -v "update_prices.py\|migrate_to_qdrant.py"; echo "$DAILY"; echo "$WEEKLY"; echo "$DISCOVER"; echo "$MIGRATE") | crontab -

echo "Cron jobs đã được cài đặt:"
crontab -l | grep -E "update_prices|migrate_to_qdrant"
