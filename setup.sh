#!/bin/bash
# ============================================
# Pokemon Card System - One-Click VPS Setup
# Clone xong chạy: bash setup.sh
# ============================================

set -e
APP_DIR=$(cd "$(dirname "$0")" && pwd)
echo "📁 Project dir: $APP_DIR"

# 1. Tạo virtual environment + cài thư viện
echo "🐍 Tạo virtual environment..."
python3 -m venv "$APP_DIR/venv"
source "$APP_DIR/venv/bin/activate"
pip install -r "$APP_DIR/requirements.txt"

# 2. Cài Playwright browsers (cho scraper)
echo "🌐 Cài Playwright browsers..."
playwright install chromium
playwright install-deps chromium

# 3. Tạo thư mục logs
mkdir -p "$APP_DIR/logs"

# 4. Setup cron jobs
echo "⏰ Cài đặt cron jobs..."
bash "$APP_DIR/scripts/setup_cron.sh"

# 5. Nhắc tạo file .env
if [ ! -f "$APP_DIR/.env" ]; then
    echo ""
    echo "⚠️  Chưa có file .env! Hãy tạo file .env với nội dung:"
    echo "   QDRANT_URL=..."
    echo "   QDRANT_API_KEY=..."
    echo "   GEMINI_API_KEY=..."
    echo "   R2_ACCESS_KEY=..."
    echo ""
fi

echo ""
echo "✅ Setup hoàn tất!"
echo "👉 Bước tiếp theo:"
echo "   1. Tạo/chỉnh file .env (nếu chưa có)"
echo "   2. Chạy API:  pm2 start $APP_DIR/venv/bin/python --name pokemon-api -- $APP_DIR/run.py"
echo "   3. Setup Nginx + SSL theo deployment_guide"
