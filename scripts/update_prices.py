"""
Pokemon Card Price Update Automation (MongoDB Direct).

Usage:
  python scripts/update_prices.py --update    # Daily: price-only tu LIST -> MongoDB
  python scripts/update_prices.py --full      # Weekly: full scrape DETAIL -> MongoDB
  python scripts/update_prices.py --discover  # Weekly: tim expansion moi
  python scripts/update_prices.py --update --dry-run  # Test, khong ghi DB
"""

import argparse
import json
import os
import sys
import re
import logging
import time
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

# Import scraper functions
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_DIR)

from scraper.scrape_scrydex import create_browser, get_card_links, scrape_card_detail, random_delay
from app.database import get_sync_db
from app.response_transformer import transform_card_for_mongo
from app.image_downloader import _make_card_id

# Config
DATA_DIR = os.path.join(PROJECT_DIR, "data")
MANIFEST_PATH = os.path.join(DATA_DIR, "manifest.json")

# Fields populated by enrichment scripts — must NOT be overwritten by scraper
ENRICHMENT_FIELDS = {
    "TCG-1month-prices", "TCG-3month-prices", "TCG-6month-prices",
    "TCG-1year-prices", "TCG-all-prices",
    "TCG-1month-forecast-prices", "TCG-3month-forecast-prices",
    "TCG-6month-forecast-prices", "TCG-1year-forecast-prices",
    "TCG-all-forecast-prices",
    "CM-1month-prices", "CM-3month-prices", "CM-6month-prices",
    "CM-1year-prices", "CM-all-prices",
    "CM-1month-forecast-prices", "CM-3month-forecast-prices",
    "CM-6month-forecast-prices", "CM-1year-forecast-prices",
    "CM-all-forecast-prices",
    "gradedPrices",
    "predictedPrice",
}


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)]
    )


def load_manifest():
    if not os.path.exists(MANIFEST_PATH):
        return {}
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("stores", {})


def update_manifest(stores_data):
    if os.path.exists(MANIFEST_PATH):
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {"stores": {}}
    data["stores"] = stores_data
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def cmd_update(dry_run=False):
    """Daily price update -- scrape list page, update MongoDB directly."""
    logging.info("Starting Daily Price Update (--update) -> MongoDB")
    stores = load_manifest()
    db = get_sync_db()
    if db is None:
        logging.error("Cannot connect to MongoDB!")
        return

    today_str = datetime.now().strftime("%Y-%m-%d")

    with sync_playwright() as p:
        browser, context = create_browser(p)
        page = context.new_page()

        for store_id, info in stores.items():
            url = info.get("url")
            if not url:
                continue

            logging.info(f"Processing store: {store_id}")

            try:
                entries = get_card_links(page, url)
            except Exception as e:
                logging.error(f"Error scraping list page for {store_id}: {e}")
                continue

            updated_count = 0
            for entry in entries:
                base_code = entry.get("card_id", "")
                new_price = entry.get("list_price", "")

                if not base_code or not new_price:
                    continue

                if not dry_run:
                    result = db.cards.update_many(
                        {
                            "store_id": store_id,
                            "card_id": {"$regex": f"^{re.escape(base_code)}_"}
                        },
                        {"$set": {
                            "currentPrice": new_price,
                            "_meta.price_last_checked": today_str
                        }}
                    )
                    if result.modified_count > 0:
                        updated_count += result.modified_count

            logging.info(f"  {store_id}: Updated {updated_count} card prices.")
            random_delay()

        browser.close()
    logging.info("Done Daily Price Update.")


def cmd_full(dry_run=False, specific_stores=None):
    """Weekly full scrape -- scrape card details, upsert to MongoDB."""
    logging.info("Starting Weekly Full Scrape (--full) -> MongoDB")
    stores = load_manifest()
    db = get_sync_db()
    if db is None:
        logging.error("Cannot connect to MongoDB!")
        return

    with sync_playwright() as p:
        browser, context = create_browser(p)
        page = context.new_page()

        target_stores = specific_stores if specific_stores else list(stores.keys())

        for store_id in target_stores:
            if store_id not in stores:
                continue
            info = stores[store_id]
            url = info.get("url")
            if not url:
                continue

            logging.info(f"Processing store: {store_id}")
            logging.info(f"Scraping {url} ...")

            try:
                entries = get_card_links(page, url)
            except Exception as e:
                logging.error(f"Error scraping list page for {store_id}: {e}")
                continue

            # Deduplicate by href
            seen = set()
            unique_entries = []
            for e in entries:
                if e["href"] not in seen:
                    seen.add(e["href"])
                    unique_entries.append(e)

            cards_count = 0
            for i, entry in enumerate(unique_entries):
                logging.info(f"  [{i+1}/{len(unique_entries)}] {entry['name_number']}")
                try:
                    detail = scrape_card_detail(page, entry["href"])

                    # Process each variant
                    variants = detail.get("variants", [])
                    if not variants:
                        variants = [{"name": "normal", "label": "Normal", "image": ""}]

                    for variant in variants:
                        variant_name = variant.get("name", "normal")
                        card_id = _make_card_id(detail, variant_name)

                        doc = transform_card_for_mongo(detail, store_id, card_id)

                        if not dry_run:
                            base_fields = {}
                            enrichment_init = {}

                            for k, v in doc.items():
                                if k == "_id":
                                    continue
                                elif k in ENRICHMENT_FIELDS:
                                    enrichment_init[k] = v
                                else:
                                    base_fields[k] = v

                            db.cards.update_one(
                                {"_id": doc["_id"]},
                                {
                                    "$set": base_fields,
                                    "$setOnInsert": enrichment_init
                                },
                                upsert=True
                            )

                        cards_count += 1

                except Exception as e:
                    logging.error(f"Error scraping card {entry['href']}: {e}")

                random_delay()

            # Update expansion info
            if not dry_run:
                db.expansions.update_one(
                    {"_id": store_id},
                    {"$set": {
                        "store_id": store_id,
                        "set_code": info.get("set_code"),
                        "url": url,
                        "total_cards": cards_count,
                        "last_scraped": datetime.now(timezone.utc).isoformat()
                    }},
                    upsert=True
                )

            logging.info(f"  Done {store_id}: Upserted {cards_count} card variants.")

        browser.close()

    # Create indexes
    if not dry_run:
        logging.info("Creating indexes...")
        db.cards.create_index("store_id")
        db.cards.create_index("card_id")
        db.cards.create_index("cardName")
        try:
            db.cards.create_index(
                [("cardName", "text"), ("cardNameEn", "text")],
                default_language="none"
            )
        except Exception:
            pass  # Index may already exist

    logging.info("Done Weekly Full Scrape.")


def cmd_discover(dry_run=False):
    """Weekly discover -- find new expansions on Scrydex."""
    logging.info("Starting Weekly Discover (--discover)")
    stores = load_manifest()

    with sync_playwright() as p:
        browser, context = create_browser(p)
        page = context.new_page()

        logging.info("Scraping Expansions page...")
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

        logging.info(f"Found {len(unique_expansions)} expansions on web.")

        known_urls = {info.get("url") for info in stores.values() if info.get("url")}
        new_expansions = [exp for exp in unique_expansions if exp["url"] not in known_urls]

        if not new_expansions:
            logging.info("No new expansions found.")
            browser.close()
            return

        logging.info(f"Found {len(new_expansions)} new expansions!")
        browser.close()

    new_store_ids = []
    for exp in new_expansions:
        url = exp["url"]
        parts = url.rstrip("/").split("/")
        set_code = parts[-1]
        store_id = parts[-2]

        logging.info(f"New expansion: {exp['name']} ({url})")
        if not dry_run:
            stores[store_id] = {
                "set_code": set_code,
                "url": url
            }
            new_store_ids.append(store_id)

    if not dry_run and new_store_ids:
        update_manifest(stores)
        logging.info("Updated manifest.json. Starting full scrape for new expansions...")
        cmd_full(dry_run=False, specific_stores=new_store_ids)
    else:
        logging.info("Dry-run: Skipping scrape and manifest update.")


# ═══════════════════════════════════════════
# RESCRAPE MODE — One-time full overwrite
# ═══════════════════════════════════════════

PROGRESS_FILE = os.path.join(PROJECT_DIR, "rescrape_progress.json")


def _load_progress():
    """Load rescrape progress from file."""
    if not os.path.exists(PROGRESS_FILE):
        return {"completed_stores": [], "current_store": None, "current_card_index": 0}
    with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_progress(progress):
    """Save rescrape progress to file."""
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def cmd_rescrape(dry_run=False, limit=None):
    """
    One-time full re-scrape — ghi đè TẤT CẢ fields (bao gồm enrichment).

    Khác với --full:
    - --full dùng $setOnInsert cho enrichment fields → không ghi đè nếu đã tồn tại
    - --rescrape dùng $set cho ALL fields → ghi đè toàn bộ, fill fields trống

    Có resume logic: nếu bị gián đoạn, chạy lại sẽ skip stores đã xong.
    """
    logging.info("=" * 60)
    logging.info("RESCRAPE MODE — One-time full overwrite")
    logging.info("=" * 60)

    stores = load_manifest()
    db = get_sync_db()
    if db is None:
        logging.error("Cannot connect to MongoDB!")
        return

    # Load progress
    progress = _load_progress()
    completed_stores = set(progress.get("completed_stores", []))
    resume_store = progress.get("current_store")
    resume_card_idx = progress.get("current_card_index", 0)

    all_store_ids = list(stores.keys())
    total_stores = len(all_store_ids)

    # Limit số stores nếu có --limit
    if limit and limit > 0:
        # Lọc bỏ stores đã completed trước khi limit
        remaining = [s for s in all_store_ids if s not in completed_stores]
        if len(remaining) > limit:
            logging.info(f"--limit {limit}: chỉ xử lý {limit} expansion(s) chưa xong")
            limited_set = set(remaining[:limit])
            all_store_ids = [s for s in all_store_ids if s in completed_stores or s in limited_set]

    logging.info(f"Total expansions in manifest: {total_stores}")
    logging.info(f"Already completed: {len(completed_stores)}")
    if resume_store:
        logging.info(f"Resuming from: {resume_store} at card index {resume_card_idx}")

    with sync_playwright() as p:
        browser, context = create_browser(p)
        page = context.new_page()

        for store_idx, store_id in enumerate(all_store_ids):
            # Skip completed stores
            if store_id in completed_stores:
                logging.info(f"[{store_idx+1}/{total_stores}] {store_id} — SKIPPED (already done)")
                continue

            info = stores[store_id]
            url = info.get("url")
            if not url:
                continue

            logging.info(f"[{store_idx+1}/{total_stores}] Processing: {store_id}")
            logging.info(f"  URL: {url}")

            # Save current store in progress
            progress["current_store"] = store_id
            progress["current_card_index"] = 0
            _save_progress(progress)

            try:
                entries = get_card_links(page, url)
            except Exception as e:
                logging.error(f"  Error scraping list page for {store_id}: {e}")
                continue

            # Deduplicate
            seen = set()
            unique_entries = []
            for e in entries:
                if e["href"] not in seen:
                    seen.add(e["href"])
                    unique_entries.append(e)

            logging.info(f"  Found {len(unique_entries)} unique cards")

            # Determine start index (for resume)
            start_idx = 0
            if store_id == resume_store and resume_card_idx > 0:
                start_idx = resume_card_idx
                logging.info(f"  Resuming from card index {start_idx}")

            cards_count = 0
            errors_count = 0

            for i in range(start_idx, len(unique_entries)):
                entry = unique_entries[i]
                logging.info(f"  [{i+1}/{len(unique_entries)}] {entry['name_number']}")

                try:
                    detail = scrape_card_detail(page, entry["href"])

                    # Process each variant
                    variants = detail.get("variants", [])
                    if not variants:
                        variants = [{"name": "normal", "label": "Normal", "image": ""}]

                    for variant in variants:
                        variant_name = variant.get("name", "normal")
                        card_id = _make_card_id(detail, variant_name)

                        doc = transform_card_for_mongo(detail, store_id, card_id)

                        if not dry_run:
                            # RESCRAPE: $set ALL fields (ghi đè toàn bộ)
                            all_fields = {k: v for k, v in doc.items() if k != "_id"}

                            db.cards.update_one(
                                {"_id": doc["_id"]},
                                {"$set": all_fields},
                                upsert=True
                            )

                        cards_count += 1

                except Exception as e:
                    logging.error(f"  Error scraping card {entry['href']}: {e}")
                    errors_count += 1

                # Save progress every 5 cards
                if (i + 1) % 5 == 0:
                    progress["current_card_index"] = i + 1
                    _save_progress(progress)

                random_delay()

            # Mark store as completed
            progress["completed_stores"] = list(completed_stores | {store_id})
            progress["current_store"] = None
            progress["current_card_index"] = 0
            _save_progress(progress)
            completed_stores.add(store_id)

            # Update expansion info
            if not dry_run:
                db.expansions.update_one(
                    {"_id": store_id},
                    {"$set": {
                        "store_id": store_id,
                        "set_code": info.get("set_code"),
                        "url": url,
                        "total_cards": cards_count,
                        "last_rescrape": datetime.now(timezone.utc).isoformat()
                    }},
                    upsert=True
                )

            logging.info(f"  ✅ Done {store_id}: {cards_count} cards, {errors_count} errors")

        browser.close()

    # Create indexes
    if not dry_run:
        logging.info("Creating indexes...")
        db.cards.create_index("store_id")
        db.cards.create_index("card_id")
        db.cards.create_index("cardName")
        try:
            db.cards.create_index(
                [("cardName", "text"), ("cardNameEn", "text")],
                default_language="none"
            )
        except Exception:
            pass

    logging.info("=" * 60)
    logging.info("RESCRAPE COMPLETE")
    logging.info(f"Total stores processed: {len(completed_stores)}/{total_stores}")
    logging.info(f"Progress file: {PROGRESS_FILE}")
    logging.info("You can delete rescrape_progress.json now.")
    logging.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Pokemon Card Price Updater (MongoDB Direct)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--update", action="store_true", help="Daily: price-only from LIST -> MongoDB")
    group.add_argument("--full", action="store_true", help="Weekly: full scrape DETAIL -> MongoDB")
    group.add_argument("--discover", action="store_true", help="Weekly: find new expansions")
    group.add_argument("--rescrape", action="store_true", help="One-time: full overwrite ALL fields (incl. enrichment)")
    parser.add_argument("--dry-run", action="store_true", help="Test mode, no DB writes")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of expansions to process (for testing)")

    args = parser.parse_args()
    setup_logging()

    if args.update:
        cmd_update(dry_run=args.dry_run)
    elif args.full:
        cmd_full(dry_run=args.dry_run)
    elif args.discover:
        cmd_discover(dry_run=args.dry_run)
    elif args.rescrape:
        cmd_rescrape(dry_run=args.dry_run, limit=args.limit)


if __name__ == "__main__":
    main()

