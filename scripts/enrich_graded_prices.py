"""
Graded Prices Enrichment Script.

Extracts graded price history (PSA, CGC, BGS, etc.) from `_meta.price_history_raw`
and populates the `gradedPrices` array in the card documents.

Usage:
  python scripts/enrich_graded_prices.py
"""
import os
import sys
import logging
from datetime import datetime

# Setup paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_DIR)

from app.database import get_sync_db

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("enrich_graded")

GRADED_COMPANIES = {"PSA", "CGC", "BGS", "TAG", "ACE", "AGS", "SGC"}

def extract_graded_prices_from_card(card):
    """Parses price_history_raw to extract graded prices."""
    price_history_raw = card.get("_meta", {}).get("price_history_raw", [])
    if not price_history_raw:
        return []

    graded_list = []
    
    for chart in price_history_raw:
        chart_id = chart.get("chart_id", "")
        # Check if the chart belongs to a graded company
        # E.g. me3-50_PSA_holofoil_history -> PSA
        parts = chart_id.split("_")
        if len(parts) < 3:
            continue
            
        company = parts[1]
        if company not in GRADED_COMPANIES:
            continue
            
        series_list = chart.get("series", [])
        for series in series_list:
            grade_name = series.get("name", "")  # E.g. "PSA 10" or "PSA 9"
            raw_data = series.get("data", [])
            
            # Format history: filter out null values and format as [date, price]
            formatted_history = []
            latest_price = None
            
            for point in raw_data:
                if len(point) >= 2:
                    date_str = point[0]
                    price_val = point[1]
                    if price_val is not None:
                        try:
                            price_float = float(price_val)
                            formatted_history.append([date_str, price_float])
                            latest_price = price_float
                        except ValueError:
                            pass
            
            if formatted_history:
                price_str = f"${latest_price:.2f}" if latest_price is not None else "$0.00"
                graded_list.append({
                    "grade": grade_name,
                    "price": price_str,
                    "history": formatted_history
                })
                
    return graded_list

def run():
    db = get_sync_db()
    if db is None:
        logger.error("Could not connect to MongoDB.")
        return

    query = {"_meta.price_history_raw": {"$exists": True}}
    total = db.cards.count_documents(query)
    logger.info(f"Scanning {total} cards for graded price history...")

    updated_count = 0
    for card in db.cards.find(query).batch_size(50):
        graded_prices = extract_graded_prices_from_card(card)
        if graded_prices:
            db.cards.update_one(
                {"_id": card["_id"]},
                {"$set": {"gradedPrices": graded_prices}}
            )
            updated_count += 1
            if updated_count % 50 == 0 or updated_count == 1:
                logger.info(f"Enriched {updated_count} cards with graded prices. (Latest: {card['cardName']})")

    logger.info(f"Done! Enriched {updated_count} cards with graded prices.")

if __name__ == "__main__":
    run()
