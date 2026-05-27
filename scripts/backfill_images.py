"""
Backfill cardImage URLs from Scrydex into MongoDB.
Only visits card pages to extract variant image URLs.
Much faster than full rescrape since it skips price/attack parsing.

Usage:
  python scripts/backfill_images.py                # 3 workers (default)
  python scripts/backfill_images.py --workers 3    # custom workers
  python scripts/backfill_images.py --dry-run      # test without writing
"""

import argparse
import json
import os
import sys
import logging
import time
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_DIR)

from scraper.scrape_scrydex import create_browser, get_card_links, random_delay
from app.database import get_sync_db

# --- Config ---
PROGRESS_FILE = os.path.join(PROJECT_DIR, "backfill_images_progress.json")
MANIFEST_FILE = os.path.join(PROJECT_DIR, "data", "manifest.json")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(PROJECT_DIR, "logs", "backfill_images.log"))
    ]
)


def load_manifest():
    with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
        return json.load(f).get("stores", {})


def _load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"completed_stores": []}


def _save_progress(progress):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def extract_image_only(page, card_url):
    """Visit card page and extract ONLY variant image URLs. Much faster than full scrape."""
    full_url = f"https://scrydex.com{card_url}" if card_url.startswith("/") else card_url
    page.goto(full_url, wait_until="domcontentloaded")
    page.wait_for_selector("[data-controller='card']", timeout=15000)

    # Extract only variant images - minimal JS evaluation
    images = page.evaluate("""() => {
        const variants = document.querySelectorAll('[data-card-target="variant"]');
        return Array.from(variants).map(v => ({
            name: v.getAttribute('data-variant-name'),
            image: v.getAttribute('data-variant-image')
        }));
    }""")

    return images


def _make_card_id(name_number, variant_name):
    """Reconstruct card_id from name_number and variant."""
    import re
    nn = name_number.strip()
    match = re.search(r'#(.+)$', nn)
    number = match.group(1).strip() if match else nn
    # Get set code from the card page URL
    return f"{number}_{variant_name}"


def _backfill_one_expansion(store_id, url, dry_run, progress_lock):
    """Worker: backfill cardImage for one expansion."""
    from playwright.sync_api import sync_playwright
    db = get_sync_db()

    with sync_playwright() as p:
        browser, context = create_browser(p)
        page = context.new_page()

        try:
            entries = get_card_links(page, url)
        except Exception as e:
            logging.error(f"[{store_id}] Error getting card links: {e}")
            browser.close()
            return 0

        seen = set()
        unique_entries = []
        for e in entries:
            if e["href"] not in seen:
                seen.add(e["href"])
                unique_entries.append(e)

        logging.info(f"[{store_id}] {len(unique_entries)} cards to process")

        updated = 0
        errors = 0

        for i, entry in enumerate(unique_entries):
            if (i + 1) % 20 == 0:
                logging.info(f"[{store_id}] [{i+1}/{len(unique_entries)}]")

            # Retry with backoff
            images = None
            for attempt in range(3):
                try:
                    images = extract_image_only(page, entry["href"])
                    break
                except Exception as e:
                    if attempt < 2:
                        wait = (attempt + 1) * 5
                        logging.warning(f"[{store_id}] Retry {attempt+1}/3 for {entry['href']} (wait {wait}s)")
                        time.sleep(wait)
                    else:
                        logging.error(f"[{store_id}] Failed 3x: {entry['href']}: {e}")
                        errors += 1

            if not images:
                random_delay()
                continue

            # Update MongoDB for each variant
            for variant in images:
                variant_name = variant.get("name", "normal")
                image_url = variant.get("image", "")

                if not image_url:
                    continue

                # Reconstruct the MongoDB _id
                # entry["href"] example: /pokemon/cards/spinarak/me3-1?variant=normal
                # card_id format: me3-1_normal
                href = entry["href"]
                # Extract set_code-number from href
                import re
                match = re.search(r'/([^/]+)\?variant=', href)
                if not match:
                    match = re.search(r'/([^/]+)$', href)
                
                if match:
                    raw_id = match.group(1)
                    card_id = f"{raw_id}_{variant_name}"
                    mongo_id = f"{store_id}:{card_id}"

                    if not dry_run:
                        result = db.cards.update_one(
                            {"_id": mongo_id},
                            {"$set": {"cardImage": image_url}}
                        )
                        if result.modified_count > 0:
                            updated += 1
                    else:
                        updated += 1

            random_delay()

        logging.info(f"[{store_id}] ✅ Done: {updated} updated, {errors} errors")
        browser.close()

    # Thread-safe progress update
    with progress_lock:
        progress = _load_progress()
        completed = set(progress.get("completed_stores", []))
        completed.add(store_id)
        progress["completed_stores"] = list(completed)
        _save_progress(progress)

    return updated


def main():
    parser = argparse.ArgumentParser(description="Backfill cardImage URLs from Scrydex")
    parser.add_argument("--workers", type=int, default=3, help="Parallel workers")
    parser.add_argument("--dry-run", action="store_true", help="Test without writing")
    parser.add_argument("--store", type=str, help="Process single store_id")
    parser.add_argument("--reset", action="store_true", help="Reset progress and start fresh")
    args = parser.parse_args()

    os.makedirs(os.path.join(PROJECT_DIR, "logs"), exist_ok=True)

    stores = load_manifest()
    
    if args.reset and os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)
        logging.info("Progress reset.")

    progress = _load_progress()
    completed = set(progress.get("completed_stores", []))

    if args.store:
        remaining = [args.store] if args.store not in completed else []
    else:
        remaining = [s for s in stores if s not in completed]

    # Check how many docs need images
    db = get_sync_db()
    needs_image = db.cards.count_documents({
        "$or": [
            {"cardImage": {"$exists": False}},
            {"cardImage": None},
            {"cardImage": ""}
        ]
    })
    total = db.cards.count_documents({})

    logging.info("=" * 60)
    logging.info(f"BACKFILL CARD IMAGES")
    logging.info(f"Docs needing image: {needs_image}/{total}")
    logging.info(f"Expansions remaining: {len(remaining)}/{len(stores)}")
    logging.info(f"Workers: {args.workers}")
    logging.info("=" * 60)

    if not remaining:
        logging.info("All expansions already processed!")
        return

    progress_lock = threading.Lock()
    total_updated = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {}
        for store_id in remaining:
            info = stores.get(store_id, {})
            url = info.get("url")
            if not url:
                continue
            f = pool.submit(_backfill_one_expansion, store_id, url, args.dry_run, progress_lock)
            futures[f] = store_id

        for f in as_completed(futures):
            store_id = futures[f]
            try:
                count = f.result()
                total_updated += count
            except Exception as e:
                logging.error(f"Worker error for {store_id}: {e}")

    logging.info("=" * 60)
    logging.info(f"BACKFILL COMPLETE: {total_updated} images updated")
    logging.info("=" * 60)


if __name__ == "__main__":
    main()
