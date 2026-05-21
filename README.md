# 🎴 Pokémon Card Image Search Pipeline

Hệ thống tự động cào dữ liệu thẻ bài Pokémon TCG Pocket từ Scrydex, tạo vector embedding bằng Gemini AI, lưu trữ trên Qdrant Cloud, và cung cấp API tìm kiếm ảnh thẻ bài.

## Kiến trúc hệ thống

```
Scrydex (Web) ──scraper──> JSON (metadata + image_url)
                                │
                    migrate_to_qdrant.py
                                │
                    ┌───────────┼───────────┐
                    ▼           ▼           ▼
              Download ảnh   Gemini AI    Qdrant Cloud
              (tạm thời)    (Embedding)  (Vector DB)
                    │                       │
                    └── Xóa sau khi embed ──┘
                                │
                          FastAPI Server
                          /api/search/by-image
```

## Cấu trúc thư mục

```
Embedding-image/
├── app/                    # FastAPI server + services
│   ├── main.py             # API endpoints
│   ├── config.py           # Cấu hình hệ thống
│   ├── card_store.py       # In-memory metadata store (O(1) lookup)
│   ├── qdrant_service.py   # Qdrant Cloud client
│   ├── embedding_service.py# Gemini Embedding API
│   ├── r2_service.py       # Cloudflare R2 (unknown cards)
│   └── schemas.py          # Pydantic models
├── scraper/                # Playwright scraper cho Scrydex
│   ├── scrape_scrydex.py   # Core scraper
│   └── ...
├── scripts/                # Automation scripts
│   ├── migrate_to_qdrant.py  # Download ảnh → Embed → Upsert → Cleanup
│   ├── update_prices.py      # Cập nhật giá (--update/--full/--discover)
│   └── setup_cron.sh         # Tự động cài đặt cron jobs
├── data/                   # JSON metadata (mỗi expansion 1 file)
├── logs/                   # Log files từ cron jobs
├── run.py                  # Entry point (uvicorn)
├── setup.sh                # One-click VPS setup
├── requirements.txt
└── .env                    # Biến môi trường (không commit)
```

## Triển khai lên VPS mới

### Bước 1: Clone dự án

```bash
git clone <repo-url> /root/Embedding-image
```

### Bước 2: Chạy setup tự động

```bash
bash /root/Embedding-image/setup.sh
```

Script này sẽ tự động:
- Tạo virtual environment + cài thư viện Python
- Cài Playwright browsers (cho scraper)
- Tạo thư mục logs
- Cài đặt cron jobs

### Bước 3: Tạo file `.env`

```bash
nano /root/Embedding-image/.env
```

Nội dung cần có:

```env
# Qdrant Cloud
QDRANT_URL=https://xxx.cloud.qdrant.io:6333
QDRANT_API_KEY=your_qdrant_api_key

# Gemini AI (cho API search)
GEMINI_API_KEY=your_gemini_api_key

# Gemini Migration Keys (cho batch migration, nhiều key cách nhau bởi dấu phẩy)
GEMINI_MIGRATION_KEYS=key1,key2,key3

# Cloudflare R2 (lưu ảnh unknown cards)
R2_ACCESS_KEY=your_r2_access_key
R2_SECRET_KEY=your_r2_secret_key
R2_BUCKET_NAME=your_bucket_name

# Scraper
SCRAPER_HEADLESS=true
```

### Bước 4: Khởi chạy API với PM2

```bash
pm2 start /root/Embedding-image/venv/bin/python --name pokemon-api -- /root/Embedding-image/run.py
pm2 save
```

### Bước 5: Setup Nginx + SSL

```bash
# Tạo config Nginx
sudo nano /etc/nginx/sites-available/image-search-mon-scan.limgrow.com
```

Nội dung:

```nginx
server {
    listen 80;
    server_name image-search-mon-scan.limgrow.com;
    client_max_body_size 120m;
    location / {
        proxy_pass http://127.0.0.1:1010/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
    }
}
```

```bash
# Kích hoạt site + SSL
sudo ln -s /etc/nginx/sites-available/image-search-mon-scan.limgrow.com /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d image-search-mon-scan.limgrow.com
```

## Cron Jobs (Tự động)

Được cài đặt tự động bởi `setup.sh`. Lịch chạy:

| Thời gian | Lệnh | Mô tả |
|---|---|---|
| 3:00 AM hàng ngày | `migrate_to_qdrant.py` | Tải ảnh mới → Embed → Đẩy lên Qdrant → Xóa ảnh |
| 5:00 AM hàng ngày | `update_prices.py --update` | Cập nhật giá nhanh từ trang danh sách |
| 2:00 AM thứ Hai | `update_prices.py --full` | Cào lại chi tiết đầy đủ toàn bộ thẻ |
| 4:00 AM Chủ nhật | `update_prices.py --discover` | Quét tìm bộ bài mới trên Scrydex |

### Kiểm tra logs

```bash
# Xem log migration gần nhất
tail -n 20 /root/Embedding-image/logs/daily_migrate.log

# Xem log cập nhật giá
tail -n 20 /root/Embedding-image/logs/daily_price.log

# Xem log quét bộ mới
tail -n 20 /root/Embedding-image/logs/weekly_discover.log

# Xem thời gian chạy cron
grep CRON /var/log/syslog | grep Embedding-image
```

## API Endpoints

Swagger UI: `https://image-search-mon-scan.limgrow.com/docs`

| Method | Endpoint | Mô tả |
|---|---|---|
| `POST` | `/api/search/by-image` | Tìm thẻ bằng ảnh (upload ảnh) |
| `GET` | `/api/cards` | Danh sách thẻ (phân trang) |
| `GET` | `/api/cards/{card_id}` | Xem chi tiết 1 thẻ |
| `GET` | `/api/stats` | Trạng thái hệ thống (Qdrant + Local + R2) |
| `POST` | `/api/index/download-images` | Tải ảnh từ Scrydex |
| `GET` | `/api/unknown-cards` | Danh sách ảnh chưa nhận diện (R2) |

## Quản lý trên VPS

```bash
# Xem trạng thái API
pm2 list

# Xem log realtime
pm2 logs pokemon-api

# Restart API (sau khi sửa code)
pm2 restart pokemon-api

# Kiểm tra cron jobs đã cài
crontab -l

# Kiểm tra API hoạt động
curl https://image-search-mon-scan.limgrow.com/api/stats
```
