"""
Card Metadata Store — In-memory index for Pokémon Cards.

Module này thay thế cho ChromaDB trong việc lưu trữ và truy xuất metadata.
Nó quét toàn bộ các file JSON (ví dụ: *_20260511_224335.json) lúc startup,
lưu vào RAM để có thể tra cứu với tốc độ O(1).
"""

import os
import glob
import json
import logging
from app.image_downloader import load_cards_from_json
from app.config import DATA_DIR, JSON_DATA_PATTERN

logger = logging.getLogger(__name__)

class CardMetadataStore:
    """
    Thay thế ChromaDB cho metadata lookup.
    Load tất cả JSON files → build hash maps → O(1) get.
    """
    
    def __init__(self, data_dir: str = DATA_DIR):
        # Hash maps chính
        self.card_by_id: dict[str, dict] = {}         # "me3-1_normal" → card
        self.card_by_global_id: dict[str, dict] = {}  # "perfect-order:me3-1_normal" → card
        self.cards_by_store: dict[str, list] = {}     # "perfect-order" → [cards...]
        self.all_cards: list[dict] = []               # Ordered, for pagination
        self.store_info: dict = {}                    # Metadata per store
        
        self._load_all(data_dir)
    
    def _load_all(self, data_dir: str):
        """Scan và load tất cả JSON files vào bộ nhớ."""
        logger.info(f"Đang scan JSON files trong {data_dir}...")
        
        # Load manifest
        manifest_path = os.path.join(data_dir, "manifest.json")
        manifest_data = {}
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest_data = json.load(f).get("stores", {})
                logger.info("✅ Loaded manifest.json")
            except Exception as e:
                logger.warning(f"Lỗi đọc manifest.json: {e}")

        # Tìm các file JSON data
        search_pattern = os.path.join(data_dir, JSON_DATA_PATTERN)
        json_files = glob.glob(search_pattern)
        
        if not json_files:
            logger.warning(f"Không tìm thấy file JSON nào khớp pattern {JSON_DATA_PATTERN} trong {data_dir}")
            return

        total_cards = 0
        for filepath in json_files:
            filename = os.path.basename(filepath)
            
            # Bỏ qua manifest nếu pattern matching vớ phải
            if filename == "manifest.json":
                continue
                
            # Extract store_id từ filename
            store_id = None
            
            # Cố gắng tìm trong manifest
            for s_id, s_info in manifest_data.items():
                if filename.startswith(s_info.get("file", "").replace(".json", "")):
                    store_id = s_id
                    break
                    
            if not store_id:
                # Fallback
                store_id = filename.split("_")[0]
            
            cards = load_cards_from_json(filepath)
            if not cards:
                continue
                
            self.store_info[store_id] = {
                "file": filename,
                "card_count": len(cards),
                "set_code": manifest_data.get(store_id, {}).get("set_code", cards[0].get("card_id", "").split("-")[0] if cards else "")
            }
            
            if store_id not in self.cards_by_store:
                self.cards_by_store[store_id] = []

            for raw_card in cards:
                card_id = raw_card.get("card_id")
                if not card_id:
                    continue
                
                # Build full card data (flatten top-level + metadata)
                meta_src = raw_card.get("metadata", {})
                full_card = {
                    "card_id": card_id,
                    "global_id": f"{store_id}:{card_id}",
                    "store_id": store_id,
                    "name": raw_card.get("name", ""),
                    "expansion": meta_src.get("expansion", ""),
                    "number": meta_src.get("number", ""),
                    "rarity": meta_src.get("rarity", ""),
                    "hp": meta_src.get("hp", ""),
                    "types": meta_src.get("types", ""),
                    "artist": meta_src.get("artist", ""),
                    "image_url": raw_card.get("image_url", ""),
                    "scrydex_url": meta_src.get("scrydex_url", ""),
                    "price_nm": meta_src.get("price_nm", None),
                    "variant": meta_src.get("variant", "")
                }
                
                # Lưu vào các hash map
                global_id = full_card["global_id"]
                
                # Xử lý dedup nếu bị trùng (ưu tiên record sau)
                self.card_by_id[card_id] = full_card
                self.card_by_global_id[global_id] = full_card
                self.cards_by_store[store_id].append(full_card)
                self.all_cards.append(full_card)
                
                total_cards += 1
                
        logger.info(f"✅ Đã load {total_cards} thẻ từ {len(json_files)} files (stores: {list(self.store_info.keys())})")

    def get(self, card_id: str, store_id: str = None) -> dict | None:
        """O(1) lookup. Ưu tiên global_id nếu có store_id."""
        if store_id:
            global_id = f"{store_id}:{card_id}"
            if global_id in self.card_by_global_id:
                return self.card_by_global_id[global_id]
        
        # Fallback: lookup by just card_id
        return self.card_by_id.get(card_id)
    
    def list_cards(self, offset=0, limit=20, store_id=None) -> tuple[list, int]:
        """Phân trang, có thể filter theo expansion."""
        source = self.cards_by_store.get(store_id, self.all_cards) if store_id else self.all_cards
        
        # Slice pagination
        end = offset + limit
        return source[offset:end], len(source)
    
    def get_stats(self) -> dict:
        """Tổng quan: cards, stores, expansions."""
        return {
            "total_cards": len(self.all_cards),
            "total_stores": len(self.store_info),
            "stores": self.store_info
        }
    
    def search_by_field(self, field: str, value: str) -> list[dict]:
        """Fallback search: scan qua toàn bộ data (O(N)) để tìm."""
        results = []
        value_lower = str(value).lower()
        
        for card in self.all_cards:
            if field == "card_id_prefix":
                if str(card.get("card_id", "")).startswith(value):
                    results.append(card)
            elif field in card:
                if value_lower in str(card[field]).lower():
                    results.append(card)
                    
        return results
