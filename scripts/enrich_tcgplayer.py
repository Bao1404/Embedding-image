"""
TCGPlayer Price History Enrichment Script.

Queries Scrydex purchase redirects to resolve TCGPlayer Product IDs,
fetches annual price histories, and updates MongoDB.

Usage:
  python scripts/enrich_tcgplayer.py --limit 10    # Enrich 10 cards for testing
  python scripts/enrich_tcgplayer.py               # Run for all cards without limit
"""
import os
import sys
import re
import time
import random
import logging
import argparse
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs
import requests

# Setup paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_DIR)

from app.database import get_sync_db

# Configure logging to stdout
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("enrich_tcgplayer")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html"
}

def get_tcgplayer_product_id(card_code, variant="normal"):
    """Fetch redirect location of the purchase link to resolve TCGPlayer ID."""
    purchase_url = f"https://scrydex.com/pokemon/cards/{card_code}/purchase?type=tcgplayer&variant={variant}"
    try:
        res = requests.get(purchase_url, headers=HEADERS, allow_redirects=False, timeout=10)
        if res.status_code == 302:
            location = res.headers.get('location', '')
            if "error=Purchase+URL+not+found" in location:
                logger.warning(f"Purchase URL not found on Scrydex for {card_code} ({variant})")
                return None, None
            
            # Extract product ID
            parsed = urlparse(location)
            params = parse_qs(parsed.query)
            u_param = params.get('u', [None])[0]
            if u_param:
                product_id_match = re.search(r'/product/(\d+)', u_param)
                if product_id_match:
                    return product_id_match.group(1), u_param
            
            # Fallback direct matching on location header
            product_id_match = re.search(r'/product/(\d+)', location)
            if product_id_match:
                return product_id_match.group(1), location
                
            return None, location
        else:
            logger.warning(f"Unexpected status code {res.status_code} for purchase link {card_code}")
    except Exception as e:
        logger.error(f"Error fetching redirect for {card_code}: {e}")
    return None, None

def fetch_tcgplayer_history(product_id):
    """Fetch price history from TCGPlayer's infinite-api."""
    url = f"https://infinite-api.tcgplayer.com/price/history/{product_id}/detailed?range=annual"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        if res.status_code == 200:
            return res.json().get("result", [])
        else:
            logger.warning(f"Failed to fetch price history for product {product_id}. Status: {res.status_code}")
    except Exception as e:
        logger.error(f"Error fetching history for product {product_id}: {e}")
    return []

def extract_tcgplayer_prices(result_list, target_finish):
    """Filters TCGPlayer results to find NM condition and target finish/variant."""
    target = target_finish.lower().replace(" ", "")
    
    # Try exact match first
    for item in result_list:
        if item.get("condition") == "Near Mint":
            variant = item.get("variant", "").lower().replace(" ", "")
            if variant == target:
                return item.get("buckets", [])
                
    # Fallback to any variant with Near Mint condition
    for item in result_list:
        if item.get("condition") == "Near Mint":
            return item.get("buckets", [])
            
    # Fallback to any condition
    for item in result_list:
        buckets = item.get("buckets", [])
        if buckets:
            return buckets
            
    return []

def format_buckets(buckets, limit_weeks=None):
    """Formats buckets into [date_str, price_float] in chronological order."""
    if limit_weeks:
        sliced = buckets[:limit_weeks]
    else:
        sliced = buckets
    
    formatted = []
    for b in sliced:
        date_str = b.get("bucketStartDate")
        price_str = b.get("marketPrice", "0")
        try:
            price_val = float(price_str)
        except ValueError:
            price_val = 0.0
        if date_str:
            formatted.append([date_str, price_val])
            
    # Reverse to make it chronological (oldest to newest)
    formatted.reverse()
    return formatted

def enrich_cards(limit=None):
    db = get_sync_db()
    if db is None:
        logger.error("Could not connect to MongoDB. Check MONGO_URI in .env.")
        return

    # Find cards that:
    # 1. Do not have tcgplayer_id marked as error/none in _meta
    # 2. Do not have TCG-all-prices populated
    query = {
        "_meta.tcgplayer_error": {"$exists": False},
        "TCG-all-prices": {"$size": 0}
    }
    
    cards = list(db.cards.find(query))
    total_found = len(cards)
    logger.info(f"Found {total_found} cards needing TCGPlayer price enrichment.")
    
    if limit:
        cards = cards[:limit]
        logger.info(f"Limit option set. Processing {len(cards)} cards.")

    success_count = 0
    skipped_count = 0
    error_count = 0

    for idx, card in enumerate(cards):
        card_id = card.get("card_id")
        store_id = card.get("store_id")
        finish = card.get("finish", "Normal")
        card_name = card.get("cardName")
        
        logger.info(f"[{idx+1}/{len(cards)}] Processing: {card_name} ({card_id}) - Finish: {finish}")
        
        # 1. Parse card code and variant
        code = card_id.split('_')[0]
        variant = card_id.split('_')[1] if '_' in card_id else "normal"
        # Scrydex variant mapping: normal -> normal, reverseHolofoil -> reverseHolofoil
        
        # 2. Fetch TCGPlayer ID from redirect
        prod_id, prod_url = get_tcgplayer_product_id(code, variant)
        
        # Delay to avoid hammering Scrydex
        time.sleep(random.uniform(0.5, 1.2))
        
        if not prod_id:
            # Mark card as no TCGPlayer URL so we don't query it again
            db.cards.update_one(
                {"_id": card["_id"]},
                {"$set": {
                    "_meta.tcgplayer_error": "not_found",
                    "_meta.tcgplayer_last_checked": datetime.now(timezone.utc).isoformat()
                }}
            )
            logger.warning(f"No TCGPlayer ID resolved for {card_name}. Marked in database.")
            skipped_count += 1
            continue
            
        logger.info(f"Resolved TCGPlayer Product ID: {prod_id}")
        
        # 3. Fetch price history from TCGPlayer
        history_results = fetch_tcgplayer_history(prod_id)
        
        # Delay to avoid hammering TCGPlayer
        time.sleep(random.uniform(0.5, 1.2))
        
        if not history_results:
            logger.warning(f"No history results retrieved for TCGPlayer Product ID: {prod_id}")
            # Mark it but don't set error so we can retry later if needed
            db.cards.update_one(
                {"_id": card["_id"]},
                {"$set": {
                    "_meta.tcgplayer_id": prod_id,
                    "_meta.tcgplayer_url": prod_url,
                    "_meta.tcgplayer_last_checked": datetime.now(timezone.utc).isoformat()
                }}
            )
            error_count += 1
            continue
            
        # 4. Extract price buckets for the correct finish
        buckets = extract_tcgplayer_prices(history_results, finish)
        if not buckets:
            logger.warning(f"No buckets found matching finish '{finish}' for product ID: {prod_id}")
            db.cards.update_one(
                {"_id": card["_id"]},
                {"$set": {
                    "_meta.tcgplayer_id": prod_id,
                    "_meta.tcgplayer_url": prod_url,
                    "_meta.tcgplayer_last_checked": datetime.now(timezone.utc).isoformat()
                }}
            )
            skipped_count += 1
            continue
            
        # 5. Format buckets — only keep "all" (API slices on-the-fly)
        tcg_all = format_buckets(buckets)
        
        # Get current price from the latest bucket to sync
        latest_price_str = buckets[0].get("marketPrice", "0")
        latest_price_val = 0.0
        try:
            latest_price_val = float(latest_price_str)
        except ValueError:
            pass
            
        current_price_str = f"${latest_price_val:.2f}"
        
        # 6. Update MongoDB document
        db.cards.update_one(
            {"_id": card["_id"]},
            {"$set": {
                "TCG-all-prices": tcg_all,
                "currentPrice": current_price_str,  # Sync latest price
                "predictedPrice": current_price_str,  # Sync prediction initial
                "priceLink": prod_url,  # Update price link to TCGPlayer product page
                "_meta.tcgplayer_id": prod_id,
                "_meta.tcgplayer_url": prod_url,
                "_meta.tcgplayer_last_enriched": datetime.now(timezone.utc).isoformat(),
                "_meta.tcgplayer_last_checked": datetime.now(timezone.utc).isoformat()
            }}
        )
        logger.info(f"Successfully enriched price history for {card_name}!")
        success_count += 1
        
    logger.info("Enrichment run summary:")
    logger.info(f"  Processed: {success_count + skipped_count + error_count}")
    logger.info(f"  Success:   {success_count}")
    logger.info(f"  Skipped:   {skipped_count}")
    logger.info(f"  Failed:    {error_count}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Enrich card documents with TCGPlayer price history")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of cards to process")
    args = parser.parse_args()
    enrich_cards(limit=args.limit)
