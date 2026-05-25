"""
Script di chuyển toàn bộ dữ liệu từ JSON files vào MongoDB.
Dữ liệu được TRANSFORM sang format data mẫu trước khi ghi.

Usage:
  python scripts/migrate_json_to_mongo.py           # Migration bình thường
  python scripts/migrate_json_to_mongo.py --clean    # Xóa data cũ rồi migrate lại
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


def migrate(clean=False):
    db = get_sync_db()
    if db is None:
        logging.error("MONGO_URI chưa được cấu hình. Hãy thêm vào .env")
        return

    # Nếu --clean: xóa toàn bộ data cũ
    if clean:
        logging.warning("🗑️  --clean flag: Xóa toàn bộ data cũ trong MongoDB...")
        db.cards.drop()
        db.expansions.drop()
        logging.info("Đã xóa xong collections cards và expansions.")

    manifest_path = os.path.join(DATA_DIR, "manifest.json")
    if not os.path.exists(manifest_path):
        logging.error(f"Không tìm thấy file manifest tại {manifest_path}")
        return

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    stores = manifest.get("stores", {})
    total_cards_inserted = 0

    logging.info(f"Bắt đầu migration {len(stores)} expansions vào MongoDB...")

    for store_id, info in stores.items():
        file_name = info.get("file")
        filepath = os.path.join(DATA_DIR, file_name)
        if not os.path.exists(filepath):
            logging.warning(f"File không tồn tại: {filepath}. Bỏ qua.")
            continue

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            raw_cards = data.get("cards", data) if isinstance(data, dict) else data
            if not isinstance(raw_cards, list):
                continue

            # Upsert Expansion
            db.expansions.update_one(
                {"_id": store_id},
                {"$set": {
                    "store_id": store_id,
                    "set_code": info.get("set_code"),
                    "url": info.get("url"),
                    "total_cards": len(raw_cards),
                    "last_migrated": datetime.now(timezone.utc).isoformat()
                }},
                upsert=True
            )

            # Upsert Cards (đã transform sang format data mẫu)
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
            logging.info(f"✅ Đã migrate {cards_count} thẻ cho {store_id}")

        except Exception as e:
            logging.error(f"Lỗi khi migrate file {filepath}: {e}")

    # Tạo Indexes
    logging.info("Tạo indexes...")
    db.cards.create_index("store_id")
    db.cards.create_index("card_id")
    db.cards.create_index("cardName")
    # Text index: dùng "none" language để tránh lỗi với tiếng Nhật
    db.cards.create_index(
        [("cardName", "text"), ("cardNameEn", "text")],
        default_language="none"
    )

    logging.info(f"🎉 Migration hoàn tất! Đã lưu {total_cards_inserted} thẻ vào MongoDB.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate JSON data to MongoDB")
    parser.add_argument("--clean", action="store_true", help="Xóa data cũ trước khi migrate")
    args = parser.parse_args()
    migrate(clean=args.clean)
