import os
import sys
import json
import base64
import uuid
import time
import datetime
import argparse
import logging
from typing import List, Tuple, Dict, Any

# Cấu hình logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Đảm bảo có thể import từ app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import DATA_DIR, IMAGES_DIR, GEMINI_MIGRATION_KEYS
from app.qdrant_service import QdrantSearchService
from app.embedding_service import GeminiEmbeddingService
from app.image_downloader import load_cards_from_json, find_json_files

NAMESPACE_POKEMON = uuid.UUID('12345678-1234-5678-1234-567812345678')

def generate_point_id(global_id: str) -> str:
    """Tạo UUID deterministic từ global_id (vd: perfect-order:me3-1_normal)"""
    return str(uuid.uuid5(NAMESPACE_POKEMON, global_id))

def fetch_existing_ids(qdrant_svc: 'QdrantSearchService') -> set:
    """Lấy toàn bộ point IDs đã tồn tại trên Qdrant bằng scroll (nhanh hơn retrieve từng cái)."""
    existing_ids = set()
    if not qdrant_svc.client:
        return existing_ids
    try:
        offset = None
        while True:
            results, next_offset = qdrant_svc.client.scroll(
                collection_name=qdrant_svc.collection_name,
                limit=256,
                offset=offset,
                with_payload=False,
                with_vectors=False,
            )
            for point in results:
                existing_ids.add(str(point.id))
            if next_offset is None:
                break
            offset = next_offset
        logger.info(f"Đã tải {len(existing_ids)} point IDs từ Qdrant để skip-check.")
    except Exception as e:
        logger.error(f"Lỗi khi scroll Qdrant: {e}")
    return existing_ids

def get_store_ids() -> List[str]:
    """Lấy danh sách store_id từ manifest.json"""
    manifest_path = os.path.join(DATA_DIR, "manifest.json")
    if not os.path.exists(manifest_path):
        logger.error(f"Không tìm thấy file {manifest_path}")
        return []
    
    with open(manifest_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        stores = data.get("stores", {})
        # stores là dict {store_id: {...info}} → chỉ cần list keys
        return list(stores.keys()) if isinstance(stores, dict) else stores

def load_image_base64(filepath: str) -> str:
    """Đọc file ảnh và chuyển sang base64"""
    with open(filepath, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

class RateLimitError(Exception):
    """Raised khi Gemini API trả về lỗi rate limit (429/RPD exhausted)."""
    pass

def embed_with_failover(image_bytes: bytes, embedding_svc: GeminiEmbeddingService) -> list:
    """
    Gọi embed_image. Nếu lỗi chứa '429' hoặc 'RESOURCE_EXHAUSTED' → raise RateLimitError
    để caller biết cần đổi key.
    Trả về None nếu lỗi khác (ảnh hỏng, response rỗng) — caller sẽ skip card đó.
    """
    try:
        vector = embedding_svc.embed_image(image_bytes)
        return vector  # Có thể là list hoặc None (lỗi không phải rate limit)
    except Exception as e:
        err_msg = str(e).lower()
        if '429' in err_msg or 'resource_exhausted' in err_msg or 'quota' in err_msg:
            raise RateLimitError(f"Rate limit detected: {e}")
        raise  # Lỗi khác (network, etc.) → raise bình thường

def migrate_store(
    store_id: str, 
    qdrant_svc: QdrantSearchService, 
    embedding_svc: GeminiEmbeddingService,
    existing_ids: set,
    dry_run: bool = False,
    session_limit: int = 950,
    current_count: int = 0
) -> int:
    """
    Migrate toàn bộ cards của một store vào Qdrant.
    Raise RateLimitError khi key hiện tại bị rate limit → caller đổi key.
    Trả về current_count mới sau khi migrate.
    """
    logger.info(f"Đang xử lý store: {store_id}")
    
    # Tìm JSON files thuộc store này
    all_json_files = find_json_files(DATA_DIR, filter_name=store_id)
    if not all_json_files:
        logger.warning(f"Không tìm thấy file JSON nào cho store {store_id}")
        return current_count

    # Dùng cùng hàm load_cards_from_json đã có sẵn — đảm bảo card_id nhất quán
    all_cards = []
    for jf in all_json_files:
        cards = load_cards_from_json(jf)
        all_cards.extend(cards)
    
    if not all_cards:
        logger.warning(f"Không tìm thấy cards nào cho store {store_id}")
        return current_count

    logger.info(f"Tìm thấy {len(all_cards)} cards cho store {store_id}")
    
    skipped = 0
    points_to_upsert: List[Tuple[str, List[float], Dict[str, Any]]] = []
    
    for card in all_cards:
        if current_count >= session_limit:
            break
            
        card_id = card.get("card_id")
        if not card_id:
            skipped += 1
            continue
            
        # Tìm ảnh tương ứng — dùng đúng tên file giống image_downloader
        # File ảnh: images/<store_id>/<card_id>.jpg
        image_dir = os.path.join(IMAGES_DIR, store_id)
        safe_name = card_id.replace("/", "_").replace("\\", "_")
        image_path = os.path.join(image_dir, f"{safe_name}.jpg")
        
        if not os.path.exists(image_path):
            # Thử .png
            image_path_png = os.path.join(image_dir, f"{safe_name}.png")
            if os.path.exists(image_path_png):
                image_path = image_path_png
            else:
                logger.warning(f"Không tìm thấy ảnh cho card {card_id} ({store_id})")
                skipped += 1
                continue
            
        # Tạo payload
        global_id = f"{store_id}:{card_id}"
        point_id = generate_point_id(global_id)
        
        # Kiểm tra nhanh bằng set đã pre-fetch (không cần gọi API từng cái)
        if point_id in existing_ids:
            skipped += 1
            continue
        
        try:
            with open(image_path, "rb") as f:
                image_bytes = f.read()
                
            if dry_run:
                vector = [0.0] * GeminiEmbeddingService.VECTOR_DIM
            else:
                vector = embed_with_failover(image_bytes, embedding_svc)
                time.sleep(1)  # Pacing to avoid hitting 100 RPM limit
                
            if not vector:
                logger.warning(f"Không embed được card {card_id} (ảnh lỗi?). Bỏ qua.")
                skipped += 1
                continue
            
            meta = card.get("metadata", {})
            payload = {
                "card_id": card_id,
                "store_id": store_id,
                "global_id": global_id,
                "name": card.get("name", meta.get("name", "")),
                "expansion": meta.get("expansion", ""),
                "number": meta.get("number", ""),
                "rarity": meta.get("rarity", ""),
                "image_url": card.get("image_url", meta.get("image_url", "")),
                "variant": meta.get("variant", ""),
            }
            
            points_to_upsert.append((point_id, vector, payload))
            existing_ids.add(point_id)  # Đánh dấu đã xử lý để không retry
            current_count += 1
            
        except RateLimitError:
            # Flush batch đã có trước khi raise
            if points_to_upsert and not dry_run:
                qdrant_svc.upsert_batch(points_to_upsert, batch_size=50)
                points_to_upsert.clear()
            raise  # Để caller đổi key
            
        except Exception as e:
            logger.error(f"Lỗi khi xử lý card {card_id}: {e}")
            skipped += 1
            
        # Batch upsert every 50 points to prevent data loss if interrupted
        if len(points_to_upsert) >= 50 and not dry_run:
            qdrant_svc.upsert_batch(points_to_upsert, batch_size=50)
            points_to_upsert.clear()
            
    if dry_run:
        logger.info(f"[DRY RUN] Sẽ upsert {len(points_to_upsert)} points cho {store_id}. Skipped: {skipped}")
        return current_count
        
    # Flush remaining
    if points_to_upsert:
        qdrant_svc.upsert_batch(points_to_upsert, batch_size=50)
        logger.info(f"Hoàn thành migrate batch cuối cho store {store_id}. Skipped: {skipped}")
        
    return current_count

def main():
    parser = argparse.ArgumentParser(description="Migrate local Pokémon cards to Qdrant Cloud via Gemini Embedding")
    parser.add_argument("--store", type=str, help="Chỉ migrate một store_id cụ thể")
    parser.add_argument("--dry-run", action="store_true", help="Chỉ kiểm tra, không upsert thực sự")
    parser.add_argument("--rebuild", action="store_true", help="Xóa collection cũ trước khi migrate")
    parser.add_argument("--session-limit", type=int, default=1000, help="Giới hạn số thẻ migrate mỗi key (default 1000 = RPD limit Gemini Embedding 2)")
    args = parser.parse_args()

    # Kiểm tra có migration keys không
    if not GEMINI_MIGRATION_KEYS:
        logger.error("Không có GEMINI_API_KEY_MIGRATION nào được cấu hình.")
        return
    logger.info(f"Có {len(GEMINI_MIGRATION_KEYS)} migration key(s) khả dụng.")

    qdrant_svc = QdrantSearchService()
    if not qdrant_svc.client:
        logger.error("Không thể kết nối Qdrant. Vui lòng kiểm tra .env")
        return
        
    if args.rebuild and not args.dry_run:
        qdrant_svc.delete_collection()
        qdrant_svc._ensure_collection()
        
    store_ids = [args.store] if args.store else get_store_ids()
    
    if not store_ids:
        logger.error("Không có store nào để xử lý.")
        return
        
    # Pre-fetch toàn bộ IDs đã tồn tại trên Qdrant (1 lần duy nhất)
    existing_ids = set() if args.dry_run else fetch_existing_ids(qdrant_svc)
    
    # ═══ Multi-key failover: chỉ dùng 1 key/ngày, đổi key khi bị rate limit ═══
    # Mục đích: Key 1 dùng hôm nay → Key 2 để dành cho ngày mai (khi Key 1 chưa reset)
    # Chỉ failover sang Key 2 nếu Key 1 bị rate limit NGAY TỪ ĐẦU
    total_embedded = 0
    store_list = list(store_ids)
    
    for key_index, api_key in enumerate(GEMINI_MIGRATION_KEYS):
        key_label = f"Key #{key_index + 1}/{len(GEMINI_MIGRATION_KEYS)}"
        logger.info(f"\n{'='*50}")
        logger.info(f"🔑 Thử {key_label} (***{api_key[-6:]})")
        logger.info(f"{'='*50}")
        
        embedding_svc = GeminiEmbeddingService(api_key=api_key)
        if not embedding_svc.client:
            logger.warning(f"{key_label} không hợp lệ. Thử key tiếp theo...")
            continue
        
        key_count = 0
        key_rate_limited = False
        all_stores_done = True
        
        for store_id in store_list:
            try:
                key_count = migrate_store(
                    store_id, 
                    qdrant_svc, 
                    embedding_svc,
                    existing_ids=existing_ids,
                    dry_run=args.dry_run, 
                    session_limit=args.session_limit,
                    current_count=key_count
                )
                if key_count >= args.session_limit:
                    logger.info(f"⏸️  {key_label} đã đạt session limit ({key_count} thẻ). Dừng phiên.")
                    all_stores_done = False
                    break
                    
            except RateLimitError as e:
                logger.warning(f"⚠️  {key_label} bị rate limit: {e}")
                key_rate_limited = True
                all_stores_done = False
                break
        
        total_embedded += key_count
        
        if key_rate_limited and key_count == 0:
            # Key này bị limit ngay từ đầu → thử key tiếp theo
            logger.info(f"↪️  {key_label} chưa reset. Chuyển sang key tiếp...")
            continue
        else:
            # Key này đã hoạt động (dù đạt limit hoặc xong hết) → DỪNG, không dùng key tiếp
            if all_stores_done:
                logger.info(f"✅ Đã xử lý xong tất cả stores với {key_label}.")
            else:
                logger.info(f"💾 {key_label} đã embed {key_count} thẻ. Để dành key còn lại cho ngày mai.")
            break
    
    # ═══ Report ═══
    if total_embedded == 0:
        logger.info("Không có thẻ mới nào cần embed. Tất cả đã tồn tại trên Qdrant.")
    else:
        logger.info(f"\n{'='*50}")
        logger.info(f"📊 Tổng kết: Đã embed {total_embedded} thẻ mới trong phiên này.")
        logger.info(f"{'='*50}")
    
    if not args.dry_run:
        info = qdrant_svc.get_collection_info()
        if info:
            logger.info("============== MIGRATION REPORT ==============")
            logger.info(f"Collection status: {info['status']}")
            logger.info(f"Total points: {info['points_count']}")
            logger.info("==============================================")

if __name__ == "__main__":
    main()
