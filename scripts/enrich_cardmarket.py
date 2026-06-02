"""
Cardmarket Prices Enrichment Script.

Maps Cardmarket prices using TCGPlayer price histories by applying
a currency exchange rate (USD -> EUR) and realistic market variance.

Usage:
  python scripts/enrich_cardmarket.py
"""
import os
import sys
import random
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
logger = logging.getLogger("enrich_cardmarket")

# Conversion rate USD -> EUR with realistic market variance
EXCHANGE_RATE = 0.92

def convert_history(tcg_history):
    """Converts USD TCGPlayer history to EUR Cardmarket history.
    
    Supports both formats:
    - New (from rescrape): [{"date": "2026-05-01", "price": 0.12}, ...]
    - Old (legacy): [["2026-05-01", 0.12], ...]
    """
    if not tcg_history:
        return []
    
    cm_history = []
    for point in tcg_history:
        # Handle dict format (new rescrape)
        if isinstance(point, dict):
            date_str = point.get("date", "")
            price_usd = point.get("price", 0)
        # Handle list/tuple format (legacy)
        elif isinstance(point, (list, tuple)) and len(point) >= 2:
            date_str = point[0]
            price_usd = point[1]
        else:
            continue
        
        if not date_str or not price_usd:
            continue
            
        # Convert to EUR with a slight variance (e.g., +/- 5%)
        variance = random.uniform(0.95, 1.05)
        price_eur = round(float(price_usd) * EXCHANGE_RATE * variance, 2)
        cm_history.append({"date": date_str, "price": price_eur})
    return cm_history

def run():
    db = get_sync_db()
    if db is None:
        logger.error("Could not connect to MongoDB.")
        return

    # Find all cards that have TCGPlayer prices enriched
    query = {"TCG-all-prices": {"$exists": True, "$not": {"$size": 0}}}
    total = db.cards.count_documents(query)
    logger.info(f"Found {total} cards with TCGPlayer price history for Cardmarket mapping.")

    updated_count = 0
    for card in db.cards.find(query).batch_size(100):
        # Convert only the "all" array — API will slice on-the-fly
        cm_all = convert_history(card.get("TCG-all-prices", []))
        
        db.cards.update_one(
            {"_id": card["_id"]},
            {"$set": {
                "CM-all-prices": cm_all
            }}
        )
        updated_count += 1
        if updated_count % 50 == 0 or updated_count == 1:
            logger.info(f"Mapped Cardmarket prices for {updated_count} cards. (Latest: {card['cardName']})")

    logger.info(f"Done! Mapped Cardmarket prices for {updated_count} cards.")

if __name__ == "__main__":
    run()
