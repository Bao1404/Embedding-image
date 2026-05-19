"""
Pokémon Card Price Update Automation.

Usage:
  python scripts/update_prices.py --update    # Daily: price-only từ LIST
  python scripts/update_prices.py --full      # Weekly: full scrape DETAIL
  python scripts/update_prices.py --discover  # Weekly: tìm expansion mới
  python scripts/update_prices.py --update --dry-run  # Test, không ghi file
"""

import argparse
import json
import os
import sys
import logging
import time
from datetime import datetime
from playwright.sync_api import sync_playwright

# Import scraper functions
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_DIR)

from scraper.scrape_scrydex import create_browser, get_card_links, scrape_card_detail, random_delay

# Config
DATA_DIR = os.path.join(PROJECT_DIR, "data")
BACKUP_DIR = os.path.join(DATA_DIR, "backup")
MANIFEST_PATH = os.path.join(DATA_DIR, "manifest.json")

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)]
    )

def normalize_price(price_str):
    """Chuẩn hóa giá thành float để so sánh."""
    if not price_str:
        return None
    cleaned = price_str.replace("$", "").replace(",", "").strip()
    try:
        return round(float(cleaned), 2)
    except ValueError:
        return None

def price_changed(old_price, new_price):
    """So sánh 2 giá sau khi normalize."""
    old_val = normalize_price(old_price)
    new_val = normalize_price(new_price)
    if old_val is None and new_val is None:
        return False
    if old_val is None or new_val is None:
        return True
    return old_val != new_val

def safe_write_json(filepath, data):
    """Ghi JSON an toàn: .tmp → rename (tránh corrupt). Backup chuyển vào data/backup/."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    
    filename = os.path.basename(filepath)
    tmp = filepath + ".tmp"
    bak = os.path.join(BACKUP_DIR, filename + ".bak")
    
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    if os.path.exists(filepath):
        os.replace(filepath, bak)
    os.replace(tmp, filepath)

def load_json_with_recovery(filepath):
    """Đọc JSON. Nếu lỗi (corrupt), tự động đọc từ file backup."""
    if not os.path.exists(filepath):
        return {}
    
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        filename = os.path.basename(filepath)
        bak_path = os.path.join(BACKUP_DIR, filename + ".bak")
        
        logging.warning(f"⚠️ File gốc bị lỗi cấu trúc ({filename}). Đang khôi phục từ backup...")
        if os.path.exists(bak_path):
            try:
                with open(bak_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    logging.info(f"✅ Đã khôi phục thành công từ {bak_path}")
                    return data
            except Exception as bak_e:
                logging.error(f"❌ File backup cũng bị lỗi: {bak_e}")
                return {}
        else:
            logging.error(f"❌ Không tìm thấy file backup tại {bak_path}")
            return {}

def load_manifest():
    data = load_json_with_recovery(MANIFEST_PATH)
    return data.get("stores", {})

def update_manifest(stores_data):
    if os.path.exists(MANIFEST_PATH):
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {"stores": {}}
    data["stores"] = stores_data
    safe_write_json(MANIFEST_PATH, data)

def cmd_update(dry_run=False):
    """Daily price update — chỉ parse trang LIST."""
    logging.info("Bắt đầu Daily Price Update (--update)")
    stores = load_manifest()
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    with sync_playwright() as p:
        browser, context = create_browser(p)
        page = context.new_page()
        
        for store_id, info in stores.items():
            file_name = info.get("file")
            url = info.get("url")
            if not file_name or not url:
                continue
                
            logging.info(f"Processing store: {store_id}")
            filepath = os.path.join(DATA_DIR, file_name)
            if not os.path.exists(filepath):
                logging.warning(f"File không tồn tại: {filepath}")
                continue
                
            old_data = load_json_with_recovery(filepath)
            if not old_data:
                logging.error(f"Lỗi: Không thể lấy dữ liệu cho {filepath}, bỏ qua.")
                continue
                
            old_cards = old_data.get("cards", [])
            old_lookup = {}
            for card in old_cards:
                cid = card.get("_list_data", {}).get("card_id", "")
                if cid:
                    old_lookup[cid] = card
                    
            logging.info(f"Đang parse {url} ...")
            try:
                new_entries = get_card_links(page, url)
            except Exception as e:
                logging.error(f"Lỗi khi cào list page: {e}")
                continue
                
            changed_count = 0
            new_count = 0
            
            for entry in new_entries:
                cid = entry["card_id"]
                if cid in old_lookup:
                    card = old_lookup[cid]
                    list_data = card.get("_list_data", {})
                    old_price = list_data.get("list_price", "")
                    new_price = entry["list_price"]
                    
                    if price_changed(old_price, new_price):
                        if "price_log" not in card:
                            card["price_log"] = []
                        card["price_log"].append({
                            "date": today_str,
                            "list_price": new_price,
                            "trend": entry["trend"],
                            "source": "list"
                        })
                        changed_count += 1
                        
                    list_data["list_price"] = new_price
                    list_data["price_trend"] = entry["trend"]
                    card["price_last_checked"] = today_str
                else:
                    new_count += 1
                    
            logging.info(f"{store_id}: Đã cập nhật {changed_count} giá thay đổi. {new_count} card mới chưa scrape detail.")
            
            old_data["price_updated_at"] = today_str
            if not dry_run:
                safe_write_json(filepath, old_data)
                
            random_delay()
            
        browser.close()
    logging.info("Hoàn thành Daily Price Update.")

def cmd_full(dry_run=False, specific_stores=None):
    """Weekly full scrape — scrape chi tiết từng thẻ."""
    logging.info("Bắt đầu Weekly Full Scrape (--full)")
    stores = load_manifest()
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    with sync_playwright() as p:
        browser, context = create_browser(p)
        page = context.new_page()
        
        target_stores = specific_stores if specific_stores else stores.keys()
        
        for store_id in target_stores:
            if store_id not in stores:
                continue
            info = stores[store_id]
            file_name = info.get("file")
            url = info.get("url")
            if not file_name or not url:
                continue
                
            logging.info(f"Processing store: {store_id}")
            filepath = os.path.join(DATA_DIR, file_name)
            old_data = load_json_with_recovery(filepath)
            
            old_cards = old_data.get("cards", [])
            old_lookup = {}
            for card in old_cards:
                cid = card.get("_list_data", {}).get("card_id", "")
                if cid:
                    old_lookup[cid] = card
                    
            logging.info(f"Đang parse {url} ...")
            try:
                new_entries = get_card_links(page, url)
            except Exception as e:
                logging.error(f"Lỗi khi cào list page: {e}")
                continue
                
            seen = set()
            unique_entries = [e for e in new_entries if not (e["href"] in seen or seen.add(e["href"]))]
            
            new_cards = []
            for i, entry in enumerate(unique_entries):
                logging.info(f"  [{i+1}/{len(unique_entries)}] {entry['name_number']}")
                try:
                    detail = scrape_card_detail(page, entry["href"])
                    detail["_list_data"] = {
                        "card_id": entry["card_id"],
                        "list_price": entry["list_price"],
                        "price_trend": entry["trend"],
                        "image_url": entry["image_url"],
                    }
                    detail["price_last_checked"] = today_str
                    
                    cid = entry["card_id"]
                    # Luôn giữ lại 1 bản ghi mới nhất của hôm nay khi chạy --full
                    detail["price_log"] = [{
                        "date": today_str,
                        "list_price": entry["list_price"],
                        "trend": entry["trend"],
                        "source": "detail",
                        "pricing": detail.get("pricing", [])
                    }]
                        
                    new_cards.append(detail)
                except Exception as e:
                    logging.error(f"Lỗi khi cào card {entry['href']}: {e}")
                    if entry["card_id"] in old_lookup:
                        new_cards.append(old_lookup[entry["card_id"]])
                        
                random_delay()
                
            if "scraped_at" not in old_data:
                old_data["scraped_at"] = datetime.now().isoformat()
            old_data["price_updated_at"] = today_str
            old_data["total_cards"] = len(new_cards)
            old_data["cards"] = new_cards
            
            if not dry_run:
                safe_write_json(filepath, old_data)
                
        browser.close()
    logging.info("Hoàn thành Weekly Full Scrape.")

def cmd_discover(dry_run=False):
    """Weekly discover — tìm expansion mới trên Scrydex."""
    logging.info("Bắt đầu Weekly Discover (--discover)")
    stores = load_manifest()
    
    with sync_playwright() as p:
        browser, context = create_browser(p)
        page = context.new_page()
        
        logging.info("Đang parse trang Expansions...")
        page.goto("https://scrydex.com/pokemon/expansions", wait_until="domcontentloaded")
        page.wait_for_selector("a[href*='/pokemon/expansions/']", timeout=15000)
        time.sleep(3)
        
        expansions = page.evaluate('''() => {
            const links = document.querySelectorAll('a[href*="/pokemon/expansions/"]');
            return Array.from(links).map(a => ({
                name: a.textContent.trim(),
                href: a.getAttribute('href'),
                url: a.href
            })).filter(e => e.href.split('/').length >= 5);
        }''')
        
        seen = set()
        unique_expansions = []
        for exp in expansions:
            if exp["url"] not in seen:
                seen.add(exp["url"])
                unique_expansions.append(exp)
                
        logging.info(f"Tìm thấy {len(unique_expansions)} expansions trên web.")
        
        known_urls = {info.get("url") for info in stores.values() if info.get("url")}
        new_expansions = [exp for exp in unique_expansions if exp["url"] not in known_urls]
        
        if not new_expansions:
            logging.info("Không có expansion mới nào.")
            browser.close()
            return
            
        logging.info(f"Phát hiện {len(new_expansions)} expansion mới!")
        browser.close()
        
    new_store_ids = []
    for exp in new_expansions:
        url = exp["url"]
        parts = url.rstrip("/").split("/")
        slug = parts[-2] + "_" + parts[-1]
        set_code = parts[-1]
        store_id = parts[-2]
        
        logging.info(f"Expansion mới: {exp['name']} ({url})")
        if not dry_run:
            stores[store_id] = {
                "file": f"{slug}.json",
                "set_code": set_code,
                "url": url
            }
            new_store_ids.append(store_id)
            
    if not dry_run and new_store_ids:
        update_manifest(stores)
        logging.info("Đã cập nhật manifest.json. Bắt đầu scrape full cho các expansion mới...")
        cmd_full(dry_run=False, specific_stores=new_store_ids)
    else:
        logging.info("Dry-run: Bỏ qua việc scrape và cập nhật manifest.")

def main():
    parser = argparse.ArgumentParser(description="Pokémon Card Price Updater")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--update", action="store_true", help="Daily: price-only từ LIST")
    group.add_argument("--full", action="store_true", help="Weekly: full scrape DETAIL")
    group.add_argument("--discover", action="store_true", help="Weekly: tìm expansion mới")
    parser.add_argument("--dry-run", action="store_true", help="Test mode, không ghi file")
    
    args = parser.parse_args()
    setup_logging()
    
    if args.update:
        cmd_update(dry_run=args.dry_run)
    elif args.full:
        cmd_full(dry_run=args.dry_run)
    elif args.discover:
        cmd_discover(dry_run=args.dry_run)

if __name__ == "__main__":
    main()
