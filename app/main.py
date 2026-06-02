"""
FastAPI Application — Pokémon Card Image Search API.

Swagger UI tự động tại: http://localhost:8000/docs
Kiến trúc:
1. Qdrant Cloud (Cloud Inference) để embed & search ảnh.
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
    QdrantSearchResponse, QdrantIndexReport,
    UnknownCardItem, UnknownCardListResponse,
    UISearchResponse
)
from app.card_store_mongo import MongoCardStore
from app.image_downloader import load_cards_from_json, find_json_files, download_images
from app.qdrant_service import QdrantSearchService
from app.embedding_service import GeminiEmbeddingService
from app.r2_service import R2StorageService
from app.response_transformer import expand_price_fields_for_api


# ═══════════════════════════════════════════
# LIFESPAN: Load dữ liệu khi server khởi động
# ═══════════════════════════════════════════

card_store: MongoCardStore = None  # Global singleton
qdrant_svc: QdrantSearchService = None  # Qdrant singleton
embedding_svc: GeminiEmbeddingService = None # Gemini Embedding singleton
r2_svc: R2StorageService = None  # R2 singleton


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load MongoDB + Qdrant client + R2 khi server start."""
    global card_store, qdrant_svc, embedding_svc, r2_svc
    logger.info("Khoi tao MongoDB Card Store...")
    card_store = MongoCardStore()
    
    logger.info("Khoi tao Qdrant Search Service (Cloud)...")
    qdrant_svc = QdrantSearchService()
    
    logger.info("Khoi tao Gemini Embedding Service...")
    embedding_svc = GeminiEmbeddingService()
    
    logger.info("Khoi tao Cloudflare R2 Storage...")
    r2_svc = R2StorageService()
    if r2_svc.is_configured():
        logger.info("R2 Storage san sang (unknown cards)")
    else:
        logger.warning("R2 chua cau hinh -- unknown cards se khong duoc luu")
    
    logger.info("Server san sang!")
    logger.info("="*50)
    logger.info("🚀 API Server is running!")
    logger.info("👉 Swagger UI (Docs): http://localhost:8100/docs")
    logger.info("👉 Redoc:            http://localhost:8100/redoc")
    logger.info("="*50)
    yield
    logger.info("Server shutdown.")


# ═══════════════════════════════════════════
# FASTAPI APP
# ═══════════════════════════════════════════

app = FastAPI(
    title="🎴 Pokémon Card Image Search API (Qdrant Cloud)",
    description="""
## Demo Pipeline: Qdrant Cloud Vector Search + Local JSON Metadata

**Quy trình:**
1. `POST /api/index/download-images` → Download ảnh
2. `python scripts/migrate_to_qdrant.py` → Upload ảnh lên Qdrant Cloud
3. `POST /api/search/by-image` → Tìm kiếm ảnh qua Qdrant, query data local.
    """,
    version="3.0.0",
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
# 3. SEARCH
# ═══════════════════════════════════════════

@app.post(
    "/api/search/by-image",
    response_model=UISearchResponse,
    tags=["🔍 Search"],
    summary="Search bằng ảnh (UI Format)",
)
async def api_search_by_image(
    request: Request,
    file: UploadFile = File(..., description="Ảnh thẻ bài cần tìm"),
):
    """
    1 API call -> Gemini (Embed image) -> vector
    1 API call -> Qdrant (Vector Search), lấy top_k điểm.
    1 MongoDB lookup -> lấy info chi tiết (đúng format UI).
    """
    start_time = time.time()
    try:
        content = await file.read()
        
        # 1. Embed image qua Gemini
        query_vector = embedding_svc.embed_image(content)
        if not query_vector:
            raise HTTPException(500, "Không thể tạo embedding từ ảnh với Gemini API.")
            
        # 2. Search Qdrant
        results = qdrant_svc.search(query_vector=query_vector, top_k=5)
        search_time_ms = (time.time() - start_time) * 1000
        
        match = False
        saved_to_r2 = False
        
        from app.config import QDRANT_MATCH_THRESHOLD, QDRANT_POKEMON_THRESHOLD

        if results:
            top_hit = results[0]
            confidence = top_hit["score"]
            card_id = top_hit["card_id"]
            store_id = top_hit["store_id"]
            
            # Kiểm tra match threshold
            if confidence >= QDRANT_MATCH_THRESHOLD:
                # MongoDB lookup (await vì là async)
                meta = await card_store.get(card_id, store_id)
                if meta:
                    match = True
                    
                    # Bỏ các MongoDB keys nội bộ
                    if "_id" in meta: del meta["_id"]
                    if "store_id" in meta: del meta["store_id"]
                    if "card_id" in meta: del meta["card_id"]

                    # Expand the 4 stored arrays into 20 fields for frontend
                    meta = expand_price_fields_for_api(meta)

                    return UISearchResponse(
                        status=True,
                        path=str(request.url.path),
                        message="Card scanned successfully",
                        statusCode=201,
                        data=meta
                    )
            
            # Xử lý R2 fallback
            if not match and r2_svc and r2_svc.is_configured():
                if confidence >= QDRANT_POKEMON_THRESHOLD:
                    try:
                        r2_key = r2_svc.upload_unknown_card(
                            image_bytes=content,
                            filename=file.filename or "unknown.jpg",
                            qdrant_result={"confidence": confidence, "top_guess": card_id},
                        )
                        saved_to_r2 = True
                        logger.info(
                            f"☁️ Unknown card saved to R2 "
                            f"(confidence={confidence:.4f}): {r2_key}"
                        )
                    except Exception as e:
                        logger.error(f"R2 upload failed: {e}")
                else:
                    logger.info(f"🚫 Skipped R2 save: confidence={confidence:.4f} < {QDRANT_POKEMON_THRESHOLD}")

            return UISearchResponse(
                status=False,
                path=str(request.url.path),
                message="Card not found or below confidence threshold",
                statusCode=404,
                data={}
            )
        else:
            # Không có kết quả từ Qdrant
            return UISearchResponse(
                status=False,
                path=str(request.url.path),
                message="No search results from Qdrant",
                statusCode=404,
                data={}
            )
            
    except Exception as e:
        logger.error(f"Qdrant search failed: {traceback.format_exc()}")
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
    qdrant_stats = qdrant_svc.get_collection_info() if qdrant_svc else None
    r2_stats = r2_svc.get_stats() if r2_svc else {"enabled": False}
    return {"local": local_stats, "qdrant": qdrant_stats, "r2": r2_stats}


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
