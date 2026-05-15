"""
Google Gemini File Search Service.

Module tách biệt xử lý embedding + search qua Google Cloud.
Không ảnh hưởng đến hệ thống CLIP + ChromaDB local.
"""

import os
import json
import time
import logging
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from google import genai
from google.genai import types

from app.config import (
    GEMINI_API_KEY,
    GEMINI_EMBEDDING_MODEL,
    GEMINI_GENERATION_MODEL,
    GEMINI_STORE_DISPLAY_NAME,
    GEMINI_STORE_CACHE_FILE,
    IMAGES_DIR,
)

logger = logging.getLogger(__name__)


class GeminiFileSearchService:
    """
    Quản lý File Search Store trên Google Cloud.
    
    Flow:
    1. create_or_get_store() → tạo/load FileSearchStore
    2. upload_card_image() → upload ảnh + metadata
    3. search() → query bằng text/ảnh
    """

    def __init__(self):
        if not GEMINI_API_KEY:
            logger.warning("⚠️ GEMINI_API_KEY chưa được cấu hình trong .env")
            self.client = None
            return
        
        self.client = genai.Client(api_key=GEMINI_API_KEY)
        self.store_name: Optional[str] = None
        self._cached_doc_count = 0
        self._cache_time = 0
        self._load_cached_store()
        logger.info(f"✅ Gemini client initialized | store={self.store_name or 'chưa tạo'}")

    def _load_cached_store(self):
        """Load store name đã cache từ file local (tiết kiệm API call)."""
        if os.path.exists(GEMINI_STORE_CACHE_FILE):
            try:
                with open(GEMINI_STORE_CACHE_FILE, "r") as f:
                    data = json.load(f)
                    self.store_name = data.get("store_name")
                    logger.info(f"📂 Loaded cached store: {self.store_name}")
            except Exception:
                pass

    def _save_store_cache(self):
        """Lưu store name vào file local."""
        with open(GEMINI_STORE_CACHE_FILE, "w") as f:
            json.dump({"store_name": self.store_name}, f)

    def create_or_get_store(self) -> str:
        """
        Tạo FileSearchStore mới hoặc reuse store đã có.
        Returns: store name (VD: 'fileSearchStores/xxx')
        """
        if not self.client:
            raise RuntimeError("Gemini client chưa khởi tạo. Kiểm tra GEMINI_API_KEY.")

        # Nếu đã có cached store, verify nó còn tồn tại
        if self.store_name:
            try:
                store = self.client.file_search_stores.get(name=self.store_name)
                logger.info(f"♻️ Reuse store: {store.name}")
                return store.name
            except Exception as e:
                logger.warning(f"Store cũ không còn tồn tại, tạo mới: {e}")
                self.store_name = None

        # Tạo store mới
        store = self.client.file_search_stores.create(
            config={
                "display_name": GEMINI_STORE_DISPLAY_NAME,
                "embedding_model": GEMINI_EMBEDDING_MODEL,
            }
        )
        self.store_name = store.name
        self._save_store_cache()
        logger.info(f"🆕 Created store: {self.store_name}")
        return self.store_name

    def upload_card_image(
        self,
        image_path: str,
        card_data: dict,
    ) -> dict:
        """
        Upload 1 ảnh card vào FileSearchStore với custom metadata.

        Args:
            image_path: Đường dẫn ảnh local
            card_data: Dict chứa thông tin card (name, card_id, expansion, ...)
        """
        if not self.client:
            raise RuntimeError("Gemini client chưa khởi tạo.")

        store_name = self.create_or_get_store()
        card_id = card_data.get("card_id", "unknown")
        # Lấy store_id từ card_data (do main.py truyền vào)
        store_id = card_data.get("store_id", "unknown")

        try:
            # Flatten metadata từ card_data["metadata"] + top-level fields
            meta_src = card_data.get("metadata", {})
            flat_fields = {
                "card_id":    card_data.get("card_id", ""),
                "global_id":  f"{store_id}:{card_data.get('card_id', '')}",
                "store_id":   store_id,
                "name":       card_data.get("name", ""),
                "expansion":  meta_src.get("expansion", ""),
                "rarity":     meta_src.get("rarity", ""),
                "hp":         meta_src.get("hp", ""),
                "types":      meta_src.get("types", ""),
                "variant":    meta_src.get("variant", ""),
                "set_code":   card_data.get("card_id", "").split("-")[0],
            }
            metadata = [{"key": k, "string_value": str(v)} for k, v in flat_fields.items() if v]

            # Upload ảnh trực tiếp vào store
            operation = self.client.file_search_stores.upload_to_file_search_store(
                file=image_path,
                file_search_store_name=store_name,
                config={
                    "display_name": f"{store_id}:{card_id}",
                    "custom_metadata": metadata if metadata else None,
                },
            )

            # Chờ operation hoàn tất (polling)
            max_wait = 60  # seconds
            elapsed = 0
            while not operation.done and elapsed < max_wait:
                time.sleep(2)
                elapsed += 2
                operation = self.client.operations.get(operation)

            if operation.done:
                return {"status": "ok", "card_id": card_id}
            else:
                return {"status": "timeout", "card_id": card_id}

        except Exception as e:
            logger.error(f"Upload failed for {card_id}: {e}")
            return {"status": "error", "card_id": card_id, "error": str(e)}

    def upload_batch(
        self,
        cards: list[dict],
        image_paths: dict,
        max_workers: int = 10,
    ) -> dict:
        """
        Upload batch ảnh vào FileSearchStore (đa luồng).

        Args:
            cards: List card data dicts
            image_paths: Mapping global_id → local image path
            max_workers: Số luồng song song (mặc định 10)

        Returns:
            {"uploaded": N, "skipped": N, "errors": N, ...}
        """
        store_name = self.create_or_get_store()

        uploaded = 0
        skipped = 0
        errors = 0
        start = time.time()

        # Lấy danh sách documents đã có trong store (để skip trùng)
        existing_docs = set()
        try:
            for doc in self.client.file_search_stores.documents.list(parent=store_name):
                existing_docs.add(doc.display_name)
        except Exception:
            pass  # Store mới, chưa có documents

        # Phân loại existing_docs theo store_id
        existing_by_store: dict[str, int] = {}
        for doc_name in existing_docs:
            s_id = doc_name.split(":")[0] if ":" in doc_name else "_legacy"
            existing_by_store[s_id] = existing_by_store.get(s_id, 0) + 1

        # Chuẩn bị danh sách cards cần upload (loại skip trước)
        to_upload = []
        store_stats: dict[str, dict] = {}

        for card in cards:
            card_id = card.get("card_id", "")
            store_id = card.get("store_id", "unknown")
            global_id = f"{store_id}:{card_id}"
            if global_id not in image_paths:
                continue

            if store_id not in store_stats:
                store_stats[store_id] = {"total": 0, "uploaded": 0, "skipped": 0, "errors": 0}
            store_stats[store_id]["total"] += 1

            # Skip nếu đã upload rồi (dùng global_id)
            if global_id in existing_docs:
                skipped += 1
                store_stats[store_id]["skipped"] += 1
                continue

            to_upload.append((card, global_id, image_paths[global_id]))

        if not to_upload:
            elapsed = time.time() - start
            return {
                "uploaded": uploaded,
                "skipped": skipped,
                "errors": errors,
                "store_name": store_name,
                "time_seconds": round(elapsed, 2),
                "max_workers": max_workers,
                "existing_count": len(existing_docs),
                "existing_by_store": existing_by_store,
                "store_breakdown": store_stats,
            }

        logger.info(f"🚀 Bắt đầu upload {len(to_upload)} cards với {max_workers} luồng song song...")

        # Upload đa luồng
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_card = {}
            for card, global_id, img_path in to_upload:
                future = executor.submit(self.upload_card_image, img_path, card)
                future_to_card[future] = (card, global_id)

            for future in as_completed(future_to_card):
                card, global_id = future_to_card[future]
                card_name = card.get("name", card.get("card_id", ""))
                s_id = card.get("store_id", "unknown")
                try:
                    result = future.result()
                    if result["status"] == "ok":
                        uploaded += 1
                        store_stats[s_id]["uploaded"] += 1
                        logger.info(f"✅ [{uploaded}] Uploaded: {card_name} ({global_id})")
                    else:
                        errors += 1
                        store_stats[s_id]["errors"] += 1
                        logger.error(f"❌ Failed: {card_name} - {result.get('error', 'timeout')}")
                except Exception as e:
                    errors += 1
                    store_stats[s_id]["errors"] += 1
                    logger.error(f"❌ Thread error: {card_name} ({global_id}) - {e}")

        elapsed = time.time() - start
        logger.info(f"🏁 Upload xong: {uploaded} OK, {skipped} skip, {errors} lỗi trong {elapsed:.1f}s")
        return {
            "uploaded": uploaded,
            "skipped": skipped,
            "errors": errors,
            "store_name": store_name,
            "time_seconds": round(elapsed, 2),
            "max_workers": max_workers,
            "existing_count": len(existing_docs),
            "existing_by_store": existing_by_store,
            "store_breakdown": store_stats,
        }

    def search(
        self,
        image_path: Optional[str] = None,
    ) -> dict:
        """
        Query Gemini với FileSearch tool để lấy card_id.

        Args:
            image_path: (Optional) Đường dẫn ảnh để Gemini phân tích

        Returns:
            {"card_id": "...", "search_time_ms": ..., "model": "...", ...}
        """
        if not self.client:
            raise RuntimeError("Gemini client chưa khởi tạo.")

        store_name = self.create_or_get_store()
        start = time.time()

        # Build content parts
        content_parts = []
        uploaded_file = None
        if image_path:
            # Upload ảnh query tạm (qua Files API)
            uploaded_file = self.client.files.upload(
                file=image_path,
                config={"display_name": "query_image"},
            )
            content_parts.append(
                types.Part.from_uri(
                    file_uri=uploaded_file.uri,
                    mime_type=uploaded_file.mime_type,
                )
            )
            
        # Prompt 2 giai đoạn: nhận diện visual → search → tự đánh giá match
        prompt = (
            "You are a Pokémon card identification expert. Follow these steps:\n\n"
            "STEP 1 - VISUAL ANALYSIS: Look at this card image carefully. Identify:\n"
            "- The Pokémon name shown on the card\n"
            "- HP value\n"
            "- Attack name(s)\n"
            "- Card number (if visible at bottom)\n\n"
            "STEP 2 - DATABASE SEARCH: Search the FileSearch knowledge base for this card. "
            "Each card has metadata: 'card_id' (e.g. 'me3-1_normal'), "
            "'store_id' (e.g. 'perfect-order'), 'global_id' (e.g. 'perfect-order:me3-1_normal').\n\n"
            "STEP 3 - VERIFY MATCH: Compare what you SEE on the card with what you FOUND in the database. "
            "Check if the name, HP, and attacks match EXACTLY.\n\n"
            "Return ONLY a JSON object:\n"
            "{\n"
            "  \"is_pokemon\": <true if the image shows a Pokémon card, false otherwise>,\n"
            "  \"visual_name\": \"<name you see on the card>\",\n"
            "  \"visual_hp\": \"<HP you see>\",\n"
            "  \"card_id\": \"<exact card_id from database, or null if no good match>\",\n"
            "  \"store_id\": \"<exact store_id from database, or null>\",\n"
            "  \"global_id\": \"<exact global_id from database, or null>\",\n"
            "  \"found_name\": \"<name of the card found in database, or null>\",\n"
            "  \"match_score\": <0-100 integer, 100=exact same card, 0=completely different>\n"
            "}\n\n"
            "CRITICAL RULES:\n"
            "- match_score 100: EXACT same card (same name, same HP, same artwork)\n"
            "- match_score 70-99: Same Pokémon but different variant/edition\n"
            "- match_score 30-69: Similar but different Pokémon (e.g. same evolution line)\n"
            "- match_score 0-29: Completely different card\n"
            "- If the name you SEE does NOT match the name you FOUND, match_score MUST be below 70\n"
            "- If no card found at all, set card_id/store_id/global_id to null and match_score to 0\n"
            "- card_id format: 'me3-6_holofoil', NOT '006/088'"
        )
        content_parts.append(types.Part.from_text(text=prompt))

        # Call Gemini với FileSearch tool + temperature=0 cho kết quả ổn định
        response = self.client.models.generate_content(
            model=GEMINI_GENERATION_MODEL,
            contents=types.Content(parts=content_parts),
            config=types.GenerateContentConfig(
                temperature=0,
                tools=[
                    types.Tool(
                        file_search=types.FileSearch(
                            file_search_store_names=[store_name],
                        )
                    )
                ]
            ),
        )

        elapsed = (time.time() - start) * 1000  # ms

        card_id = None
        store_id = None
        confidence = 0.0
        visual_name = None
        found_name = None
        is_pokemon = False
        verification_done = False  # True nếu self-verification đã chạy thành công

        # Parse JSON response (giờ là nguồn chính vì có match_score)
        if response.text:
            try:
                # Strip markdown json block if any
                clean_text = response.text.strip()
                if clean_text.startswith("```json"):
                    clean_text = clean_text[7:]
                if clean_text.startswith("```"):
                    clean_text = clean_text[3:]
                if clean_text.endswith("```"):
                    clean_text = clean_text[:-3]
                
                data = json.loads(clean_text.strip())
                card_id = data.get("card_id")
                store_id = data.get("store_id")
                visual_name = data.get("visual_name")
                found_name = data.get("found_name")
                match_score = data.get("match_score", 0)
                is_pokemon = data.get("is_pokemon", visual_name is not None)
                verification_done = True  # Parse JSON thành công = verification đã chạy
                
                # Convert match_score (0-100) → confidence (0-1)
                confidence = min(max(match_score, 0), 100) / 100.0
                
                logger.info(
                    f"🔍 Visual: '{visual_name}' | Found: '{found_name}' | "
                    f"Score: {match_score}/100 | card_id: {card_id}"
                )
                
                # Nếu match_score quá thấp → coi như không tìm thấy
                if match_score < 70:
                    logger.warning(
                        f"⚠️ Low match: visual='{visual_name}' vs found='{found_name}' "
                        f"(score={match_score}). Treating as no match."
                    )
                    card_id = None
                    store_id = None
                    
            except Exception as e:
                logger.error(f"Failed to parse Gemini response: {response.text[:200]} - Error: {e}")
                # Parse fail nhưng có response text → có thể là Pokemon card
                if response.text and len(response.text.strip()) > 10:
                    confidence = 0.2

        # FALLBACK: grounding_metadata CHỈ khi text parse FAIL hoàn toàn
        # KHÔNG dùng nếu verification đã chạy và reject (match_score < 70)
        if not card_id and not verification_done:
            if response.candidates and response.candidates[0].grounding_metadata:
                gm = response.candidates[0].grounding_metadata
                if gm.grounding_chunks:
                    for chunk in gm.grounding_chunks:
                        ctx = chunk.retrieved_context
                        if hasattr(ctx, "custom_metadata") and ctx.custom_metadata:
                            for m in ctx.custom_metadata:
                                if m.key == "card_id" and not card_id:
                                    card_id = m.string_value
                                if m.key == "store_id" and not store_id:
                                    store_id = m.string_value
                    # Grounding fallback → capped at 0.5 confidence 
                    # (vì không qua verification)
                    if card_id and confidence < 0.5:
                        confidence = 0.5

        # Cleanup query image
        if uploaded_file:
            try:
                self.client.files.delete(name=uploaded_file.name)
            except Exception:
                pass

        return {
            "card_id": card_id,
            "store_id": store_id,
            "confidence": round(confidence, 4),
            "visual_name": visual_name,
            "found_name": found_name,
            "is_pokemon": is_pokemon,
            "search_time_ms": round(elapsed, 1),
            "model": GEMINI_GENERATION_MODEL,
            "store_name": store_name,
        }

    def get_status(self) -> dict:
        """Lấy thông tin store hiện tại."""
        if not self.client:
            return {"error": "Gemini client chưa khởi tạo"}

        if not self.store_name:
            return {
                "store_name": None,
                "document_count": 0,
                "embedding_model": GEMINI_EMBEDDING_MODEL,
                "generation_model": GEMINI_GENERATION_MODEL,
                "status": "Chưa tạo store. Gọi /api/gemini/upload trước.",
            }

        # Đếm documents với cache 60s
        doc_names = []
        current_time = time.time()
        
        if current_time - self._cache_time < 60 and self._cached_doc_count > 0:
            doc_count = self._cached_doc_count
        else:
            doc_count = 0
            try:
                for doc in self.client.file_search_stores.documents.list(parent=self.store_name):
                    doc_count += 1
                    if doc_count <= 5:  # Chỉ lấy 5 cái đầu để hiển thị
                        doc_names.append(doc.display_name)
                
                self._cached_doc_count = doc_count
                self._cache_time = current_time
            except Exception as e:
                return {"error": f"Không thể đọc store: {e}"}

        return {
            "store_name": self.store_name,
            "document_count": doc_count,
            "sample_documents": doc_names,
            "embedding_model": GEMINI_EMBEDDING_MODEL,
            "generation_model": GEMINI_GENERATION_MODEL,
            "status": "ready",
        }

    def delete_store(self):
        """Xóa store (nếu cần reset)."""
        if not self.client or not self.store_name:
            return
        try:
            self.client.file_search_stores.delete(
                name=self.store_name, config={"force": True}
            )
            logger.info(f"🗑️ Deleted store: {self.store_name}")
            self.store_name = None
            if os.path.exists(GEMINI_STORE_CACHE_FILE):
                os.remove(GEMINI_STORE_CACHE_FILE)
        except Exception as e:
            logger.error(f"Delete failed: {e}")

    def delete_and_recreate_store(self) -> str:
        """Xóa store cũ và tạo lại store mới, dùng khi metadata schema thay đổi."""
        self.delete_store()
        return self.create_or_get_store()
