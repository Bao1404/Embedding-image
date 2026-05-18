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

from app.config import DATA_DIR, IMAGES_DIR, GEMINI_API_KEY_MIGRATION
from app.qdrant_service import QdrantSearchService
from app.embedding_service import GeminiEmbeddingService
from app.image_downloader import load_cards_from_json, find_json_files

NAMESPACE_POKEMON = uuid.UUID('12345678-1234-5678-1234-567812345678')

def generate_point_id(global_id: str) -> str:
    """Tạo UUID deterministic từ global_id (vd: perfect-order:me3-1_normal)"""
    return str(uuid.uuid5(NAMESPACE_POKEMON, global_id))

def get_store_ids() -> List[str]:
    """Lấy danh sách store_id từ manifest.json"""
    manifest_path = os.path.join(DATA_DIR, "manifest.json")
    if not os.path.exists(manifest_path):
        logger.error(f"Không tìm thấy file {manifest_path}")
        return []
    
    with open(manifest_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        return data.get("stores", [])

def load_image_base64(filepath: str) -> str:
    """Đọc file ảnh và chuyển sang base64"""
    with open(filepath, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

def migrate_store(
    store_id: str, 
    qdrant_svc: QdrantSearchService, 
    embedding_svc: GeminiEmbeddingService,
    dry_run: bool = False,
    session_limit: int = 950,
    current_count: int = 0
) -> int:
    """
    Migrate toàn bộ cards của một store vào Qdrant
    Trả về current_count mới sau khi migrate
    """
    logger.info(f"Đang xử lý store: {store_id}")
    
    # Tìm JSON files thuộc store này
    all_json_files = find_json_files(DATA_DIR, filter_name=store_id)
    if not all_json_files:
        logger.warning(f"Không tìm thấy file JSON nào cho store {store_id}")
        return

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
        
        # Kiểm tra xem point đã tồn tại chưa để tiết kiệm RPD khi resume
        if not dry_run:
            try:
                existing = qdrant_svc.client.retrieve(
                    collection_name=qdrant_svc.collection_name, 
                    ids=[point_id]
                )
                if existing:
                    logger.info(f"Bỏ qua card {global_id} vì đã tồn tại trên Qdrant.")
                    skipped += 1
                    continue
            except Exception as e:
                pass # Bỏ qua lỗi retrieve
        
        try:
            with open(image_path, "rb") as f:
                image_bytes = f.read()
                
            if dry_run:
                # Mock embedding for dry run
                vector = [0.0] * embedding_svc.VECTOR_DIM
            else:
                vector = embedding_svc.embed_image(image_bytes)
                time.sleep(1) # Pacing to avoid hitting 100 RPM limit
                
            if not vector:
                logger.error(f"Lỗi embed ảnh {image_path}. Bỏ qua.")
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
            current_count += 1
            
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
    parser.add_argument("--session-limit", type=int, default=950, help="Giới hạn số thẻ migrate mỗi session (default 950 để tránh hit RPD limit)")
    args = parser.parse_args()

    qdrant_svc = QdrantSearchService()
    if not qdrant_svc.client:
        logger.error("Không thể kết nối Qdrant. Vui lòng kiểm tra .env")
        return
        
    embedding_svc = GeminiEmbeddingService(api_key=GEMINI_API_KEY_MIGRATION)
    if not embedding_svc.client:
        logger.error("Không thể kết nối Gemini API. Vui lòng kiểm tra GEMINI_API_KEY hoặc GEMINI_API_KEY_MIGRATION")
        return
        
    if args.rebuild and not args.dry_run:
        qdrant_svc.delete_collection()
        qdrant_svc._ensure_collection()
        
    store_ids = [args.store] if args.store else get_store_ids()
    
    if not store_ids:
        logger.error("Không có store nào để xử lý.")
        return
        
    current_count = 0
    for store_id in store_ids:
        current_count = migrate_store(
            store_id, 
            qdrant_svc, 
            embedding_svc, 
            dry_run=args.dry_run, 
            session_limit=args.session_limit,
            current_count=current_count
        )
        if current_count >= args.session_limit:
            logger.warning(f"Đã đạt session_limit ({args.session_limit}). Dừng migrate để tránh Rate Limit.")
            
            # Tính 28h sau từ bây giờ
            next_run = datetime.datetime.now() + datetime.timedelta(hours=28)
            next_run_str = next_run.strftime('%Y-%m-%d %H:%M:%S')
            
            logger.info("============== RATE LIMIT NOTICE ==============")
            logger.info(f"Đã sử dụng ~{current_count} Gemini requests hôm nay.")
            logger.info("Giới hạn miễn phí là 1,000 RPD (Requests Per Day).")
            logger.info(f"Vui lòng đợi 28 giờ để RPD reset hoàn toàn.")
            logger.info(f"Thời điểm an toàn để chạy lại: {next_run_str}")
            logger.info("==============================================")
            break
        
    if not args.dry_run:
        info = qdrant_svc.get_collection_info()
        if info:
            logger.info("============== MIGRATION REPORT ==============")
            logger.info(f"Collection status: {info['status']}")
            logger.info(f"Total points: {info['points_count']}")
            logger.info("==============================================")

if __name__ == "__main__":
    main()
