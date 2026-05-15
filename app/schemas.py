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
    """Tổng hợp stats từ cả local và Gemini."""
    local: dict
    gemini: dict


class CardListResponse(BaseModel):
    """Response cho endpoint liệt kê cards."""
    total: int
    page: int
    limit: int
    cards: list[CardInfo]


# ═══════════════════════════════════════════
# GEMINI FILE SEARCH RESPONSES
# ═══════════════════════════════════════════

class GeminiSearchResponse(BaseModel):
    """Response từ Gemini File Search, trả về dữ liệu giống Local Search."""
    match: bool = Field(..., description="Có tìm thấy thẻ khớp không")
    best_result: Optional[SearchResult] = None
    search_time_ms: float = Field(..., example=350.0)
    model: str = Field(..., example="gemini-2.5-flash-lite")
    store_name: str = Field(..., description="FileSearchStore đã dùng")
    store_id: Optional[str] = Field(None, description="Store ID (expansion) của thẻ")
    saved_to_r2: bool = Field(False, description="Ảnh đã lưu vào R2 để review sau (khi match=False)")
    card_id: Optional[str] = Field(None, description="card_id từ Gemini (debug)")
    global_id: Optional[str] = Field(None, description="global_id đầy đủ (debug)")
    confidence: float = Field(0.0, description="Match score (0-1). Gemini tự đánh giá mức match giữa ảnh và card tìm được")
    visual_name: Optional[str] = Field(None, description="Tên Pokemon mà Gemini nhìn thấy trên ảnh")
    found_name: Optional[str] = Field(None, description="Tên card tìm được trong database")


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


class GeminiIndexReport(BaseModel):
    """Kết quả upload ảnh vào Google FileSearchStore."""
    uploaded: int
    skipped: int
    errors: int
    store_name: str
    time_seconds: float
    max_workers: int = Field(5, description="Số luồng song song đã dùng")
    existing_count: int = Field(0, description="Tổng số docs đã có trong store trước khi upload")
    existing_by_store: dict = Field(default_factory=dict, description="Số docs đã có, phân theo store_id")
    store_breakdown: dict = Field(default_factory=dict, description="Chi tiết upload/skip/error theo từng store")
