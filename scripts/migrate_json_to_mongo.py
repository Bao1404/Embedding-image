"""
Script di chuyển toàn bộ dữ liệu từ JSON files vào MongoDB.
Dữ liệu được TRANSFORM sang format data mẫu trước khi ghi.

Tự động phát hiện và skip các bộ đã import đầy đủ.

Usage:
  python scripts/migrate_json_to_mongo.py           # Migration có skip (mặc định)
  python scripts/migrate_json_to_mongo.py --clean    # Xóa data cũ rồi migrate lại
  python scripts/migrate_json_to_mongo.py --force    # Ghi đè tất cả, không skip
  python scripts/migrate_json_to_mongo.py --status   # Chỉ hiển thị trạng thái, không migrate
"""
import os
import sys
import json
import logging
import argparse
from datetime import datetime, timezone

# Setup paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_DIR)

from app.config import DATA_DIR
from app.database import get_sync_db
from app.image_downloader import _make_card_id
from app.response_transformer import transform_card_for_mongo

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def get_mongo_card_counts(db):
    """Đếm số thẻ mỗi expansion trong MongoDB."""
    counts = {}
    pipeline = [{"$group": {"_id": "$store_id", "count": {"$sum": 1}}}]
    for doc in db.cards.aggregate(pipeline):
        if doc["_id"]:
            counts[doc["_id"]] = doc["count"]
    return counts


def count_json_cards(filepath):
    """Đếm số thẻ trong 1 file JSON."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw_cards = data.get("cards", data) if isinstance(data, dict) else data
        if isinstance(raw_cards, list):
            return len(raw_cards), raw_cards
        return 0, []
    except Exception:
        return 0, []


def show_status(db, stores):
    """Hiển thị trạng thái migration của từng expansion."""
    mongo_counts = get_mongo_card_counts(db)
    total_mongo = db.cards.count_documents({})
    total_expansions_mongo = db.expansions.count_documents({})

    logging.info(f"MongoDB: {total_mongo} cards, {total_expansions_mongo} expansions")
    logging.info(f"Manifest: {len(stores)} expansions")

    complete, incomplete, missing = [], [], []

    for store_id, info in stores.items():
        filepath = os.path.join(DATA_DIR, info.get("file", ""))
        if not os.path.exists(filepath):
            continue
        json_count, _ = count_json_cards(filepath)
        if json_count == 0:
            continue

        mongo_count = mongo_counts.get(store_id, 0)
        if mongo_count == 0:
            missing.append((store_id, json_count))
        elif mongo_count < json_count:
            incomplete.append((store_id, mongo_count, json_count))
        else:
            complete.append((store_id, mongo_count, json_count))

    logging.info(f"  [OK]      Hoàn chỉnh: {len(complete)} bộ")
    logging.info(f"  [PARTIAL] Thiếu thẻ:  {len(incomplete)} bộ")
    logging.info(f"  [MISSING] Chưa có:    {len(missing)} bộ")

    if incomplete:
        logging.info("--- Bộ thiếu thẻ ---")
        for sid, mc, jc in incomplete:
            logging.info(f"  {sid}: {mc}/{jc} (thiếu {jc - mc})")

    if missing:
        logging.info(f"--- Bộ chưa import ({len(missing)} bộ) ---")
        for sid, jc in missing:
            logging.info(f"  {sid}: 0/{jc}")

    return complete, incomplete, missing


def migrate(clean=False, force=False, status_only=False):
    db = get_sync_db()
    if db is None:
        logging.error("MONGO_URI chưa được cấu hình. Hãy thêm vào .env")
        return

    # Đọc manifest
    manifest_path = os.path.join(DATA_DIR, "manifest.json")
    if not os.path.exists(manifest_path):
        logging.error(f"Không tìm thấy file manifest tại {manifest_path}")
        return

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    stores = manifest.get("stores", {})

    # --status: chỉ hiển thị trạng thái
    if status_only:
        show_status(db, stores)
        return

    # --clean: xóa toàn bộ data cũ
    if clean:
        logging.warning("🗑️ --clean flag: Xóa toàn bộ data cũ trong MongoDB...")
        db.cards.drop()
        db.expansions.drop()
        logging.info("Đã xóa xong collections cards và expansions.")

    # Lấy số lượng hiện tại trong MongoDB để skip
    mongo_counts = {} if clean else get_mongo_card_counts(db)

    total_cards_inserted = 0
    skipped = 0

    logging.info(f"Bắt đầu migration {len(stores)} expansions vào MongoDB...")

    for store_id, info in stores.items():
        file_name = info.get("file")
        filepath = os.path.join(DATA_DIR, file_name)
        if not os.path.exists(filepath):
            logging.warning(f"File không tồn tại: {filepath}. Bỏ qua.")
            continue

        try:
            json_count, raw_cards = count_json_cards(filepath)
            if json_count == 0:
                continue

            # Skip logic: nếu MongoDB đã có đủ số thẻ của bộ này
            mongo_count = mongo_counts.get(store_id, 0)
            if not force and not clean and mongo_count >= json_count:
                skipped += 1
                continue

            if mongo_count > 0 and mongo_count < json_count:
                logging.info(f"[RESUME] {store_id}: {mongo_count}/{json_count} -> import bù...")
            else:
                logging.info(f"[IMPORT] {store_id}: 0/{json_count} -> import mới...")

            # Upsert Expansion
            db.expansions.update_one(
                {"_id": store_id},
                {"$set": {
                    "store_id": store_id,
                    "set_code": info.get("set_code"),
                    "url": info.get("url"),
                    "total_cards": json_count,
                    "last_migrated": datetime.now(timezone.utc).isoformat()
                }},
                upsert=True
            )

            # Upsert Cards
            cards_count = 0
            for raw_card in raw_cards:
                variants = raw_card.get("variants", [])
                variant_name = variants[0].get("name", "normal") if variants else "normal"
                card_id = _make_card_id(raw_card, variant_name)

                # Transform sang format data mẫu
                doc = transform_card_for_mongo(raw_card, store_id, card_id)

                db.cards.update_one(
                    {"_id": doc["_id"]},
                    {"$set": doc},
                    upsert=True
                )
                cards_count += 1

            total_cards_inserted += cards_count
            logging.info(f"✅ [OK] Đã migrate {cards_count} thẻ cho {store_id}")

        except Exception as e:
            logging.error(f"Lỗi khi migrate file {filepath}: {e}")

    # Tạo Indexes
    logging.info("Tạo indexes...")
    db.cards.create_index("store_id")
    db.cards.create_index("card_id")
    db.cards.create_index("cardName")
    # Text index: dùng "none" language để tránh lỗi với tiếng Nhật
    try:
        db.cards.create_index(
            [("cardName", "text"), ("cardNameEn", "text")],
            default_language="none"
        )
    except Exception:
        pass  # Index đã tồn tại

    logging.info(f"🎉 Migration hoàn tất! Đã xử lý {total_cards_inserted} thẻ. Skipped {skipped} bộ đã đầy đủ.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate JSON data to MongoDB")
    parser.add_argument("--clean", action="store_true", help="Xóa data cũ trước khi migrate")
    parser.add_argument("--force", action="store_true", help="Ghi đè tất cả, không skip")
    parser.add_argument("--status", action="store_true", help="Chỉ hiển thị trạng thái, không migrate")
    args = parser.parse_args()
    migrate(clean=args.clean, force=args.force, status_only=args.status)
