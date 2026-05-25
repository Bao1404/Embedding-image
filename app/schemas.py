"""
Pydantic schemas cho request/response của API.
Định nghĩa rõ cấu trúc dữ liệu để Swagger UI hiển thị đẹp.
"""
from pydantic import BaseModel, Field
from typing import Optional


# ═══════════════════════════════════════════
# CARD & SEARCH RESPONSE
# ═══════════════════════════════════════════

class CardInfo(BaseModel):
    """Thông tin 1 thẻ Pokémon trả về từ search."""
    name: str = Field(..., example="Pikachu ex")
    expansion: str = Field("", example="Perfect Order")
    number: str = Field("", example="025")
    rarity: str = Field("", example="holofoil")
    hp: str = Field("", example="120")
    types: str = Field("", example="Lightning")
    artist: str = Field("", example="Mitsuhiro Arita")
    image_url: str = Field("", example="https://images.scrydex.com/pokemon/me3-25/medium")
    scrydex_url: str = Field("", example="https://scrydex.com/pokemon/cards/pikachu-ex/me3-25")
    price_nm: Optional[str] = Field(None, example="$12.50")


class SearchResult(BaseModel):
    """Một kết quả trong danh sách search."""
    rank: int = Field(..., example=1)
    score: float = Field(..., example=0.9876, description="Confidence (-1=N/A, 0-1=similarity)")
    card: CardInfo


class SearchResponse(BaseModel):
    """Response của endpoint search-by-image."""
    match: bool = Field(..., description="Có tìm thấy thẻ khớp không (score >= threshold)")
    threshold: float = Field(..., example=0.92)
    best_result: Optional[SearchResult] = None
    alternatives: list[SearchResult] = Field(default_factory=list)
    total_indexed: int = Field(..., description="Tổng số thẻ trong DB")
    search_time_ms: float = Field(..., example=42.5)


# ═══════════════════════════════════════════
# INDEX RESPONSES
# ═══════════════════════════════════════════

class DownloadReport(BaseModel):
    """Kết quả sau khi download ảnh."""
    total_cards: int
    downloaded: int
    skipped: int = Field(0, description="Đã có sẵn, bỏ qua")
    errors: int
    output_dir: str


class StoreInfo(BaseModel):
    """Thông tin store từ Local JSON"""
    file: str
    card_count: int
    set_code: str

class StatsResponse(BaseModel):
    """Tổng hợp stats từ local và Qdrant."""
    local: dict
    qdrant: dict


class CardListResponse(BaseModel):
    """Response cho endpoint liệt kê cards."""
    total: int
    page: int
    limit: int
    cards: list[CardInfo]


# ═══════════════════════════════════════════
# QDRANT CLOUD RESPONSES
# ═══════════════════════════════════════════

class QdrantSearchResponse(BaseModel):
    """Response từ Qdrant Vector Search."""
    match: bool = Field(..., description="Có tìm thấy thẻ khớp không")
    best_result: Optional[SearchResult] = None
    alternatives: list[SearchResult] = Field(default_factory=list)
    search_time_ms: float = Field(..., example=120.5)
    engine: str = Field("qdrant-gemini-emb2", description="Search engine")
    collection_name: str = Field(..., description="Tên collection trên Qdrant")
    store_id: Optional[str] = Field(None, description="Store ID (expansion) của thẻ")
    saved_to_r2: bool = Field(False, description="Ảnh đã lưu vào R2 để review sau (khi match=False)")
    card_id: Optional[str] = Field(None, description="card_id từ Qdrant")
    global_id: Optional[str] = Field(None, description="global_id đầy đủ")
    confidence: float = Field(0.0, description="CLIP cosine similarity (0-1)")


# ═══════════════════════════════════════════
# UI PRODUCTION RESPONSE
# ═══════════════════════════════════════════

class UISearchResponse(BaseModel):
    """Response chuẩn map 100% với UI Production."""
    status: bool = Field(True)
    path: str = Field("/api/search/by-image")
    message: str = Field("Card scanned successfully")
    statusCode: int = Field(201)
    data: dict = Field(..., description="Card data chuẩn format UI")


# ═══════════════════════════════════════════
# UNKNOWN CARDS (R2 Storage)
# ═══════════════════════════════════════════

class UnknownCardItem(BaseModel):
    """Một ảnh thẻ bài unknown đã lưu trên R2."""
    key: str = Field(..., example="2026-05-14T08-35-10_a1b2c3d4.jpg")
    uploaded_at: Optional[str] = Field(None, example="2026-05-14T08:35:10+07:00")
    original_filename: Optional[str] = Field(None, example="IMG_2020.jpg")
    size_bytes: int = Field(0, example=60010)
    status: str = Field("pending", example="pending")


class UnknownCardListResponse(BaseModel):
    """Danh sách ảnh unknown cards trên R2."""
    total: int
    cards: list[UnknownCardItem]


class QdrantIndexReport(BaseModel):
    """Kết quả index cards vào Qdrant."""
    uploaded: int
    skipped: int
    errors: int
    collection_name: str
    time_seconds: float
    total_in_collection: int
