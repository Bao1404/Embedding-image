import os
import sys
from dotenv import load_dotenv

# Setup paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from app.database import get_sync_db

db = get_sync_db()
if db is None:
    print("Could not connect to MongoDB.")
    sys.exit(1)

# Find a card that has price_history_raw
card = db.cards.find_one({"_meta.price_history_raw": {"$exists": True, "$not": {"$size": 0}}})
if not card:
    print("No cards with price_history_raw found.")
else:
    print("Found card:", card.get("cardName"))
    price_history = card.get("_meta", {}).get("price_history_raw", [])
    print(f"Number of price history charts: {len(price_history)}")
    for chart in price_history:
        chart_id = chart.get("chart_id", "")
        series = chart.get("series", [])
        print(f"Chart ID: {chart_id}")
        for s in series:
            name = s.get("name", "")
            data = s.get("data", [])
            print(f"  Series Name: {name}, data points: {len(data)}")
            if data:
                print(f"    First point: {data[0]}")
                print(f"    Last point: {data[-1]}")
