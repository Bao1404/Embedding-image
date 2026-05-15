"""
FastAPI Application — Pokémon Card Image Search API.

Swagger UI tự động tại: http://localhost:8000/docs
Kiến trúc:
1. Gemini File Search để embed & search ảnh.
2. Local JSON Store (O(1)) để map data.
"""

import os
import time
import tempfile
import traceback
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, UploadFile, Query, HTTPException, Request
from fastapi.responses import JSONResponse

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from app.config import DATA_DIR, IMAGES_DIR
from app.schemas import (
    SearchResult, CardInfo,
    DownloadReport, CardListResponse, StatsResponse,
    GeminiSearchResponse, GeminiIndexReport,
    UnknownCardItem, UnknownCardListResponse
)
from app.card_store import CardMetadataStore
from app.image_downloader import load_cards_from_json, find_json_files, download_images
from app.gemini_service import GeminiFileSearchService
from app.r2_service import R2StorageService


# ═══════════════════════════════════════════
# LIFESPAN: Load dữ liệu khi server khởi động
# ═══════════════════════════════════════════

card_store: CardMetadataStore = None  # Global singleton
gemini_svc: GeminiFileSearchService = None  # Gemini singleton
r2_svc: R2StorageService = None  # R2 singleton


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load JSON files + Gemini client + R2 khi server start."""
    global card_store, gemini_svc, r2_svc
    logger.info("Khoi tao Local JSON Metadata Store...")
    card_store = CardMetadataStore(DATA_DIR)
    
    logger.info("Khoi tao Gemini File Search Service (Cloud)...")
    gemini_svc = GeminiFileSearchService()
    
    logger.info("Khoi tao Cloudflare R2 Storage...")
    r2_svc = R2StorageService()
    if r2_svc.is_configured():
        logger.info("R2 Storage san sang (unknown cards)")
    else:
        logger.warning("R2 chua cau hinh -- unknown cards se khong duoc luu")
    
    logger.info("Server san sang!")
    yield
    logger.info("Server shutdown.")


# ═══════════════════════════════════════════
# FASTAPI APP
# ═══════════════════════════════════════════

app = FastAPI(
    title="🎴 Pokémon Card Image Search API (Gemini)",
    description="""
## Demo Pipeline: Gemini File Search + Local JSON Metadata

**Quy trình:**
1. `POST /api/index/download-images` → Download ảnh
2. `POST /api/gemini/upload` → Upload ảnh lên Gemini
3. `POST /api/search/by-image` → Tìm kiếm ảnh qua Gemini, query data local.
    """,
    version="2.0.0",
    lifespan=lifespan
)


# ═══════════════════════════════════════════
# GLOBAL ERROR HANDLER
# ═══════════════════════════════════════════

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
    tb_str = "".join(tb)
    logger.error(f"Unhandled error on {request.method} {request.url}:\n{tb_str}")
    return JSONResponse(
        status_code=500,
        content={
            "error": str(exc),
            "type": type(exc).__name__,
            "traceback": tb_str,
            "endpoint": str(request.url),
        }
    )


# ═══════════════════════════════════════════
# 1. TẢI ẢNH XUỐNG
# ═══════════════════════════════════════════

@app.post(
    "/api/index/download-images",
    response_model=DownloadReport,
    tags=["📥 Indexing"],
    summary="Download ảnh thẻ Pokémon từ Scrydex"
)
async def api_download_images(
    dataset: str = Query(
        "perfect-order",
        description="Tên bộ bài (filter tên file JSON). Ví dụ: perfect-order, ascended-heroes"
    )
):
    json_files = find_json_files(DATA_DIR, filter_name=dataset)
    if not json_files:
        raise HTTPException(404, f"Không tìm thấy file JSON chứa '{dataset}' trong {DATA_DIR}")
    
    total_downloaded = 0
    total_skipped = 0
    total_errors = 0
    total_cards = 0
    
    for jf in json_files:
        # Extract store_id từ tên file: perfect-order_me3.json → perfect-order
        filename = os.path.basename(jf)
        store_id = filename.split("_")[0]
        
        cards = load_cards_from_json(jf)
        if not cards:
            continue
        
        total_cards += len(cards)
        print(f"📥 Bắt đầu download {len(cards)} ảnh cho '{store_id}' (file: {filename})...")
        stats = await download_images(cards, IMAGES_DIR, store_id=store_id)
        
        total_downloaded += stats["downloaded"]
        total_skipped += stats["skipped"]
        total_errors += stats["errors"]
    
    if total_cards == 0:
        raise HTTPException(404, "Không tìm thấy cards trong file JSON")
    
    return DownloadReport(
        total_cards=total_cards,
        downloaded=total_downloaded,
        skipped=total_skipped,
        errors=total_errors,
        output_dir=IMAGES_DIR
    )


# ═══════════════════════════════════════════
# 2. GEMINI UPLOAD
# ═══════════════════════════════════════════

@app.post(
    "/api/gemini/upload",
    response_model=GeminiIndexReport,
    tags=["🌐 Gemini File Search"],
    summary="Upload ảnh vào Google Cloud FileSearchStore",
)
async def gemini_upload(
    dataset: str = Query("perfect-order", description="Tên bộ dataset"),
    max_workers: int = Query(10, ge=1, le=20, description="Số luồng upload song song"),
    rebuild: bool = Query(False, description="Xóa store cũ rồi tạo mới"),
):
    try:
        if rebuild:
            logger.info("Gemini: Xóa store cũ...")
            gemini_svc.delete_and_recreate_store()
        
        # Load cards từ JSON
        json_files = find_json_files(DATA_DIR, filter_name=dataset)
        if not json_files:
            raise HTTPException(404, f"Không tìm thấy file JSON cho '{dataset}'")
        
        unique_cards = []
        image_paths = {}
        seen = set()
        
        for jf in json_files:
            # Extract store_id từ tên file
            store_id = os.path.basename(jf).split("_")[0]
            cards = load_cards_from_json(jf)
            
            for card in cards:
                card_id = card["card_id"]
                global_id = f"{store_id}:{card_id}"
                if global_id not in seen:
                    seen.add(global_id)
                    card_copy = dict(card)
                    card_copy["store_id"] = store_id
                    unique_cards.append(card_copy)
                    
                    # Tìm ảnh trong thư mục tương ứng của store_id
                    safe_name = card_id.replace("/", "_").replace("\\", "_")
                    local_path = os.path.join(IMAGES_DIR, store_id, f"{safe_name}.jpg")
                    if os.path.exists(local_path):
                        image_paths[global_id] = local_path
        
        if not image_paths:
            raise HTTPException(404, "Chưa có ảnh. Chạy /api/index/download-images trước.")
        
        logger.info(f"Gemini: Upload {len(image_paths)} ảnh với {max_workers} luồng...")
        result = gemini_svc.upload_batch(unique_cards, image_paths, max_workers=max_workers)
        
        return GeminiIndexReport(**result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Gemini upload failed: {traceback.format_exc()}")
        raise


@app.delete(
    "/api/gemini/store",
    tags=["🌐 Gemini File Search"],
    summary="Xóa store Gemini (reset toàn bộ dữ liệu đã upload)",
)
async def gemini_delete_store():
    """Xóa FileSearchStore hiện tại. Cần upload lại từ đầu sau khi gọi."""
    try:
        store_name = gemini_svc.store_name
        if not store_name:
            return {"deleted": False, "message": "Không có store nào để xóa"}
        gemini_svc.delete_store()
        return {"deleted": True, "old_store": store_name}
    except Exception as e:
        logger.error(f"Delete store failed: {e}")
        raise HTTPException(500, f"Xóa store thất bại: {e}")


# ═══════════════════════════════════════════
# 3. SEARCH
# ═══════════════════════════════════════════

@app.post(
    "/api/search/by-image",
    response_model=GeminiSearchResponse,
    tags=["🔍 Search"],
    summary="Search bằng ảnh qua Gemini AI",
)
async def api_search_by_image(
    file: UploadFile = File(..., description="Ảnh thẻ bài cần tìm"),
):
    """
    1 API call -> Gemini, lấy store_id + card_id.
    1 Local lookup -> lấy info chi tiết.
    """
    try:
        content = await file.read()
        suffix = ".jpg" if "jpeg" in (file.content_type or "") else ".png"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        
        try:
            result = gemini_svc.search(image_path=tmp_path)
            
            card_id = result.get("card_id")
            store_id = result.get("store_id")
            match = False
            best_result = None

            if card_id:
                # 1. Tra cứu local O(1) qua card_store
                meta = card_store.get(card_id, store_id)
                
                # 2. Fallbacks nếu không có store_id hoặc card_id sai lệch
                if not meta:
                    logger.warning(f"Fallback 1: Search by exact card_id: {card_id}")
                    meta = card_store.get(card_id)
                
                if not meta:
                    logger.warning(f"Fallback 2: prefix search")
                    # Ví dụ "me3-6_holofoil" -> prefix "me3-6"
                    if "_" in card_id:
                        prefix = card_id.rsplit("_", 1)[0]
                        res = card_store.search_by_field("card_id_prefix", prefix)
                        if res:
                            meta = res[0]
                
                if not meta:
                    logger.warning(f"Fallback 3: name search")
                    res = card_store.search_by_field("name", card_id)
                    if res:
                        meta = res[0]
                
                if not meta:
                    logger.warning(f"Fallback 4: number search")
                    res = card_store.search_by_field("number", card_id)
                    if res:
                        meta = res[0]

                if meta:
                    match = True
                    card_info = CardInfo(
                        name=meta.get("name", "Unknown"),
                        expansion=meta.get("expansion", ""),
                        number=meta.get("number", ""),
                        rarity=meta.get("rarity", ""),
                        hp=meta.get("hp", ""),
                        types=meta.get("types", ""),
                        artist=meta.get("artist", ""),
                        image_url=meta.get("image_url", ""),
                        scrydex_url=meta.get("scrydex_url", ""),
                        price_nm=meta.get("price_nm", None),
                    )
                    best_result = SearchResult(
                        rank=1,
                        score=result.get("confidence", -1.0),
                        card=card_info
                    )

            # Nếu không match → lưu ảnh vào R2 nếu Gemini xác nhận đây là card Pokemon
            saved_to_r2 = False
            gemini_confidence = result.get("confidence", 0.0)
            is_pokemon = result.get("is_pokemon", False)
            
            if not match and r2_svc and r2_svc.is_configured():
                # Lưu R2 nếu: Gemini nói đây là card Pokemon, HOẶC confidence >= 0.15
                if is_pokemon or gemini_confidence >= 0.15:
                    try:
                        r2_key = r2_svc.upload_unknown_card(
                            image_bytes=content,
                            filename=file.filename or "unknown.jpg",
                            gemini_result=result,
                        )
                        saved_to_r2 = True
                        logger.info(
                            f"☁️ Unknown card saved to R2 "
                            f"(is_pokemon={is_pokemon}, confidence={gemini_confidence:.4f}, "
                            f"visual='{result.get('visual_name')}'): {r2_key}"
                        )
                    except Exception as e:
                        logger.error(f"R2 upload failed (non-blocking): {e}")
                else:
                    logger.info(
                        f"🚫 Skipped R2 save: is_pokemon={is_pokemon}, "
                        f"confidence={gemini_confidence:.4f} (likely not a Pokémon card)"
                    )

            return GeminiSearchResponse(
                match=match,
                best_result=best_result,
                search_time_ms=result["search_time_ms"],
                model=result["model"],
                store_name=result["store_name"],
                store_id=store_id,
                saved_to_r2=saved_to_r2,
                card_id=card_id,
                global_id=f"{store_id}:{card_id}" if store_id and card_id else None,
                confidence=gemini_confidence,
                visual_name=result.get("visual_name"),
                found_name=result.get("found_name"),
            )
        finally:
            os.unlink(tmp_path)
    
    except Exception as e:
        logger.error(f"Gemini search failed: {traceback.format_exc()}")
        raise


# ═══════════════════════════════════════════
# 4. CARDS DATA & STATS
# ═══════════════════════════════════════════

@app.get(
    "/api/cards/{card_id}",
    response_model=CardInfo,
    tags=["🃏 Cards"],
    summary="Xem metadata 1 thẻ theo ID"
)
async def api_get_card(card_id: str, store_id: str = None):
    meta = card_store.get(card_id, store_id)
    if not meta:
        raise HTTPException(404, f"Không tìm thấy card: {card_id}")
    
    return CardInfo(
        name=meta.get("name", "Unknown"),
        expansion=meta.get("expansion", ""),
        number=meta.get("number", ""),
        rarity=meta.get("rarity", ""),
        hp=meta.get("hp", ""),
        types=meta.get("types", ""),
        artist=meta.get("artist", ""),
        image_url=meta.get("image_url", ""),
        scrydex_url=meta.get("scrydex_url", ""),
        price_nm=meta.get("price_nm", None),
    )


@app.get(
    "/api/cards",
    response_model=CardListResponse,
    tags=["🃏 Cards"],
    summary="Liệt kê tất cả thẻ trong DB (phân trang)"
)
async def api_list_cards(
    page: int = Query(1, ge=1, description="Trang"),
    limit: int = Query(20, ge=1, le=100, description="Số item/trang"),
    store_id: str = Query(None, description="Lọc theo store_id")
):
    offset = (page - 1) * limit
    cards_raw, total = card_store.list_cards(offset=offset, limit=limit, store_id=store_id)
    
    cards = [
        CardInfo(
            name=m.get("name", "Unknown"),
            expansion=m.get("expansion", ""),
            number=m.get("number", ""),
            rarity=m.get("rarity", ""),
            hp=m.get("hp", ""),
            types=m.get("types", ""),
            artist=m.get("artist", ""),
            image_url=m.get("image_url", ""),
            scrydex_url=m.get("scrydex_url", ""),
            price_nm=m.get("price_nm", None),
        )
        for m in cards_raw
    ]
    
    return CardListResponse(total=total, page=page, limit=limit, cards=cards)


@app.get(
    "/api/stats",
    tags=["📊 Stats"],
    summary="Xem trạng thái hệ thống"
)
async def api_stats():
    local_stats = card_store.get_stats()
    gemini_stats = gemini_svc.get_status()
    r2_stats = r2_svc.get_stats() if r2_svc else {"enabled": False}
    return {"local": local_stats, "gemini": gemini_stats, "r2": r2_stats}


# ═══════════════════════════════════════════
# 5. UNKNOWN CARDS (R2 Storage)
# ═══════════════════════════════════════════

@app.get(
    "/api/unknown-cards",
    response_model=UnknownCardListResponse,
    tags=["☁️ Unknown Cards"],
    summary="Liệt kê ảnh thẻ bài chưa xác định trên R2"
)
async def api_list_unknown_cards(
    limit: int = Query(50, ge=1, le=200, description="Số lượng tối đa")
):
    """Trả về danh sách ảnh thẻ bài mà hệ thống chưa nhận diện được."""
    if not r2_svc or not r2_svc.is_configured():
        raise HTTPException(503, "R2 chưa cấu hình. Thêm R2 credentials vào .env")
    return r2_svc.list_unknown_cards(limit=limit)


@app.get(
    "/api/unknown-cards/{key}/url",
    tags=["☁️ Unknown Cards"],
    summary="Lấy link xem ảnh unknown card (presigned URL, valid 1h)"
)
async def api_get_unknown_card_url(key: str):
    """Tạo presigned URL để xem ảnh trực tiếp trên trình duyệt."""
    if not r2_svc or not r2_svc.is_configured():
        raise HTTPException(503, "R2 chưa cấu hình")
    try:
        url = r2_svc.get_unknown_card_url(f"unknown/{key}")
        return {"key": key, "url": url, "expires_in": "1 hour"}
    except Exception as e:
        raise HTTPException(500, f"Không thể tạo URL: {e}")


@app.delete(
    "/api/unknown-cards/{key}",
    tags=["☁️ Unknown Cards"],
    summary="Xóa ảnh unknown card sau khi đã review"
)
async def api_delete_unknown_card(key: str):
    """Xóa ảnh + metadata JSON trên R2."""
    if not r2_svc or not r2_svc.is_configured():
        raise HTTPException(503, "R2 chưa cấu hình")
    success = r2_svc.delete_unknown_card(f"unknown/{key}")
    if not success:
        raise HTTPException(500, f"Xóa thất bại: {key}")
    return {"deleted": True, "key": key}
