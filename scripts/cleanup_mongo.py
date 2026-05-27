"""
Cleanup MongoDB: Xoa _meta va cac field khong can thiet.
Chay sau khi rescrape hoan tat.

Usage:
  python3 scripts/cleanup_mongo.py              # Chay that
  python3 scripts/cleanup_mongo.py --dry-run    # Chi xem, khong xoa
"""
import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.database import get_sync_db


def main():
    parser = argparse.ArgumentParser(description="Cleanup MongoDB - xoa _meta")
    parser.add_argument("--dry-run", action="store_true", help="Chi xem, khong xoa")
    args = parser.parse_args()

    db = get_sync_db()
    if db is None:
        print("ERROR: Cannot connect to MongoDB!")
        return

    # --- Stats truoc cleanup ---
    stats_before = db.command("dbstats")
    total_docs = db.cards.count_documents({})
    has_meta = db.cards.count_documents({"_meta": {"$exists": True}})
    data_before = stats_before["dataSize"] / 1024 / 1024
    storage_before = stats_before["storageSize"] / 1024 / 1024

    print("=" * 60)
    print("MONGODB CLEANUP")
    print("=" * 60)
    print(f"Total documents:  {total_docs:,}")
    print(f"Docs with _meta:  {has_meta:,}")
    print(f"Data size BEFORE: {data_before:.1f}MB")
    print(f"Storage BEFORE:   {storage_before:.1f}MB")
    print()

    if args.dry_run:
        print("[DRY RUN] Skipping actual cleanup")
        print(f"Would remove _meta from {has_meta:,} documents")
        print(f"Estimated savings: ~{has_meta * 1.8 / 1024:.0f}MB")
        return

    # --- Xoa _meta ---
    print("Step 1: Removing _meta from all documents...")
    result = db.cards.update_many(
        {"_meta": {"$exists": True}},
        {"$unset": {"_meta": ""}}
    )
    print(f"  Modified: {result.modified_count:,} documents")

    # --- Stats sau cleanup ---
    stats_after = db.command("dbstats")
    data_after = stats_after["dataSize"] / 1024 / 1024
    storage_after = stats_after["storageSize"] / 1024 / 1024

    print()
    print("=" * 60)
    print("RESULT")
    print("=" * 60)
    print(f"Data size AFTER:  {data_after:.1f}MB")
    print(f"Storage AFTER:    {storage_after:.1f}MB")
    print(f"Data freed:       {data_before - data_after:.1f}MB")
    print(f"Storage freed:    {storage_before - storage_after:.1f}MB")
    print()

    # Verify
    remaining = db.cards.count_documents({"_meta": {"$exists": True}})
    if remaining == 0:
        print("VERIFIED: _meta completely removed!")
    else:
        print(f"WARNING: {remaining} docs still have _meta")


if __name__ == "__main__":
    main()
