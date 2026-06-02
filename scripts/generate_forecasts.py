"""
Gemini AI & Trend Forecasting Script.

Generates price forecasts (1 month, 3 months, 6 months, 1 year) for both
TCGPlayer and Cardmarket based on historical trends, and updates predictedPrice.

Usage:
  python scripts/generate_forecasts.py
"""
import os
import sys
import logging
import numpy as np
from datetime import datetime, timedelta

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
logger = logging.getLogger("generate_forecasts")

def forecast_series(history, weeks_ahead):
    """
    Generates a weekly price forecast using a linear regression trend
    on the most recent historical data.
    """
    if not history or len(history) < 3:
        return []
        
    # Extract dates and prices
    dates = [p.get("date") for p in history if isinstance(p, dict)]
    prices = [p.get("price") for p in history if isinstance(p, dict)]
    
    if not dates and len(history) > 0 and isinstance(history[0], list):
        # Fallback in case some data is stored as lists
        dates = [p[0] for p in history]
        prices = [p[1] for p in history]
    
    # Calculate future dates
    last_date_str = dates[-1]
    try:
        last_date = datetime.strptime(last_date_str, "%Y-%m-%d")
    except ValueError:
        last_date = datetime.now()
        
    future_dates = []
    for i in range(1, weeks_ahead + 1):
        f_date = last_date + timedelta(weeks=i)
        future_dates.append(f_date.strftime("%Y-%m-%d"))
        
    # Linear regression on the last 12 points (or all if less than 12)
    n_points = min(12, len(prices))
    recent_prices = prices[-n_points:]
    x = np.arange(n_points)
    y = np.array(recent_prices)
    
    # Fit line: y = mx + c
    slope, intercept = np.polyfit(x, y, 1)
    
    # Generate forecast
    forecast = []
    current_price = prices[-1]
    
    for i, f_date in enumerate(future_dates):
        # Calculate forecasted value
        # We project from the last index (n_points - 1) forward
        projected_idx = n_points - 1 + (i + 1)
        pred_val = slope * projected_idx + intercept
        
        # Clamp value to be realistic (no negative prices, max 300% of current price)
        min_price = max(0.01, current_price * 0.5)
        max_price = current_price * 3.0
        clamped_val = max(min_price, min(max_price, pred_val))
        
        # Round to 2 decimal places
        forecast.append([f_date, round(clamped_val, 2)])
        
    return forecast

def run():
    db = get_sync_db()
    if db is None:
        logger.error("Could not connect to MongoDB.")
        return

    # Find cards that have price history
    query = {"TCG-all-prices": {"$exists": True, "$not": {"$size": 0}}}
    total = db.cards.count_documents(query)
    logger.info(f"Generating forecasts for {total} cards...")

    updated_count = 0
    for card in db.cards.find(query).batch_size(100):
        # 1. Forecast TCGPlayer — only the full "all" forecast (52 weeks)
        tcg_hist = card.get("TCG-all-prices", [])
        tcg_forecast = forecast_series(tcg_hist, 52)
        
        # 2. Forecast Cardmarket — only the full "all" forecast (52 weeks)
        cm_hist = card.get("CM-all-prices", [])
        cm_forecast = forecast_series(cm_hist, 52)
        
        # 3. Update predictedPrice using 1-month slice (first 4 weeks of forecast)
        predicted_price_str = card.get("currentPrice", "$0.00")
        if tcg_forecast and len(tcg_forecast) >= 4:
            pred_val = tcg_forecast[3][1]  # 4th week = ~1 month ahead
            predicted_price_str = f"${pred_val:.2f}"
        elif tcg_forecast:
            pred_val = tcg_forecast[-1][1]
            predicted_price_str = f"${pred_val:.2f}"
            
        db.cards.update_one(
            {"_id": card["_id"]},
            {"$set": {
                "TCG-all-forecast-prices": tcg_forecast,
                "CM-all-forecast-prices": cm_forecast,
                "predictedPrice": predicted_price_str
            }}
        )
        updated_count += 1
        if updated_count % 50 == 0 or updated_count == 1:
            logger.info(f"Generated forecasts for {updated_count} cards. (Latest: {card['cardName']} -> predictedPrice: {predicted_price_str})")

    logger.info(f"Done! Generated forecasts for {updated_count} cards.")

if __name__ == "__main__":
    run()
