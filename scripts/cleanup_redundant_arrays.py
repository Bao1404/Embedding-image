"""
Cleanup Redundant Price Arrays in MongoDB.

Removes the 16 redundant price arrays (1month, 3month, 6month, 1year) for
both TCG and CM (prices + forecasts), keeping only the "all" variants.

The API layer will slice these on-the-fly when serving responses.

This is expected to free ~200 MB of storage on MongoDB Atlas Free Tier.

Usage:
  python scripts/cleanup_redundant_arrays.py --dry-run   # Preview changes
  python scripts/cleanup_redundant_arrays.py              # Execute cleanup
"""
import os
import sys
import logging
import argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_DIR)

from app.database import get_sync_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("cleanup_arrays")

# These 16 fields are redundant — they are subsets of the "all" variants
FIELDS_TO_REMOVE = [
    "TCG-1month-prices",
    "TCG-3month-prices",
    "TCG-6month-prices",
    "TCG-1year-prices",
    "TCG-1month-forecast-prices",
    "TCG-3month-forecast-prices",
    "TCG-6month-forecast-prices",
    "TCG-1year-forecast-prices",
    "CM-1month-prices",
    "CM-3month-prices",
    "CM-6month-prices",
    "CM-1year-prices",
    "CM-1month-forecast-prices",
    "CM-3month-forecast-prices",
    "CM-6month-forecast-prices",
    "CM-1year-forecast-prices",
]

# These 4 fields are kept in the database
FIELDS_KEPT = [
    "TCG-all-prices",
    "TCG-all-forecast-prices",
    "CM-all-prices",
    "CM-all-forecast-prices",
]


def run(dry_run=False):
    db = get_sync_db()
    if db is None:
        logger.error("Cannot connect to MongoDB!")
        return

    # Get current DB stats
    stats = db.command("dbstats")
    data_mb_before = stats["dataSize"] / 1024 / 1024
    logger.info(f"Database size BEFORE cleanup: {data_mb_before:.1f} MB")
    logger.info(f"Fields to REMOVE: {len(FIELDS_TO_REMOVE)}")
    logger.info(f"Fields to KEEP:   {FIELDS_KEPT}")

    if dry_run:
        # Count how many documents have these fields
        for field in FIELDS_TO_REMOVE:
            count = db.cards.count_documents({field: {"$exists": True}})
            logger.info(f"  {field}: exists in {count} documents")
        logger.info("DRY RUN — no changes made.")
        return

    # Build the $unset operation — removes all 16 redundant fields in one go
    unset_dict = {field: "" for field in FIELDS_TO_REMOVE}

    # Since update_many({}) can exceed the space quota due to oplog/journaling,
    # we process it in batches. Find all documents that have at least one field to remove.
    from pymongo import UpdateOne
    
    query = {"$or": [{field: {"$exists": True}} for field in FIELDS_TO_REMOVE]}
    total_to_process = db.cards.count_documents(query)
    logger.info(f"Found {total_to_process} documents to clean up.")

    batch_size = 500
    processed = 0
    modified = 0

    cursor = db.cards.find(query, {"_id": 1}).batch_size(batch_size)
    batch = []
    
    for doc in cursor:
        batch.append(UpdateOne({"_id": doc["_id"]}, {"$unset": unset_dict}))
        if len(batch) >= batch_size:
            res = db.cards.bulk_write(batch, ordered=False)
            modified += res.modified_count
            processed += len(batch)
            logger.info(f"Processed {processed}/{total_to_process} documents...")
            batch = []
            
    if batch:
        res = db.cards.bulk_write(batch, ordered=False)
        modified += res.modified_count
        processed += len(batch)
        logger.info(f"Processed {processed}/{total_to_process} documents...")

    logger.info(f"Modified {modified} documents in total.")

    # Verify
    stats_after = db.command("dbstats")
    data_mb_after = stats_after["dataSize"] / 1024 / 1024
    saved = data_mb_before - data_mb_after
    logger.info(f"Database size AFTER cleanup: {data_mb_after:.1f} MB")
    logger.info(f"Space saved: {saved:.1f} MB")
    logger.info("Done! Redundant arrays removed successfully.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Remove redundant price arrays from MongoDB")
    parser.add_argument("--dry-run", action="store_true", help="Preview without making changes")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
