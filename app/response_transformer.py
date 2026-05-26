"""
Response Transformer — Chuyển đổi raw Scrydex card → format data mẫu.

Dùng khi ghi vào MongoDB. API chỉ cần đọc và trả về, không transform thêm.
"""

import uuid
from urllib.parse import quote_plus

# ═══════════════════════════════════════════
# ENERGY ICON MAPPING
# ═══════════════════════════════════════════

ENERGY_ICONS = {
    "grass": "https://upload-services.limgrow.com/uploads/l-cross-003-fitness/c29a2524-1211-4ade-beb3-70d92a25e500.png",
    "fire": "https://upload-services.limgrow.com/uploads/l-cross-003-fitness/80a61512-2dbf-47ce-a5ef-dd102a6bbd2e.png",
    "water": "https://upload-services.limgrow.com/uploads/l-cross-003-fitness/81151e3e-71ff-4b53-a6e2-703adb9e214b.png",
    "lightning": "https://upload-services.limgrow.com/uploads/l-cross-003-fitness/d36c1ef5-369b-4242-b6b3-c40592efee8a.png",
    "psychic": "https://upload-services.limgrow.com/uploads/l-cross-003-fitness/03ddc0b8-0733-4093-b9f2-3c262f395900.png",
    "fighting": "https://upload-services.limgrow.com/uploads/l-cross-003-fitness/9c36255f-5970-4445-a597-798d52a01448.png",
    "dark": "https://upload-services.limgrow.com/uploads/l-cross-003-fitness/b4270153-0cea-4efd-ad41-1f93681cfd52.png",
    "metal": "https://upload-services.limgrow.com/uploads/l-cross-003-fitness/1b2bacdc-1e88-416a-b12d-0e6a6c3dafef.png",
    "fairy": "https://upload-services.limgrow.com/uploads/l-cross-003-fitness/db342b7b-76a4-473a-aaa7-7bd202cf2de0.png",
    "dragon": "https://upload-services.limgrow.com/uploads/l-cross-003-fitness/e22887d6-d791-47a2-97d3-44ab34f25e0e.png",
    "colorless": "https://upload-services.limgrow.com/uploads/l-cross-003-fitness/fd7234a2-3e06-42dd-a9bf-b2db513f9080.png",
}

# ═══════════════════════════════════════════
# EXPANSION → YEAR MAPPING
# ═══════════════════════════════════════════

EXPANSION_YEARS = {
    "Ascended Heroes": "2026",
    "Perfect Order": "2026",
    "Phantasmal Flames": "2025",
    "Fantastical Parade": "2025",
    "Mega Dream EX": "2026",
    "Mega Shine": "2025",
    "Nihil Zero": "2026",
    "Ninja Spinner": "2026",
    "Paldean Wonders": "2025",
}


# ═══════════════════════════════════════════
# PRICE HISTORY PARSING
# ═══════════════════════════════════════════

from datetime import datetime as _dt, timedelta as _td


def _parse_price_history(raw_card: dict, variant_name: str = "normal") -> dict:
    """
    Parse price_history raw từ Chartkick data → TCG-*-prices theo time range.

    Trả về dict với keys: TCG-1month-prices, TCG-3month-prices, ...
    Mỗi value là list of {"date": "YYYY-MM-DD", "price": float}.
    """
    result = {
        "TCG-1month-prices": [],
        "TCG-3month-prices": [],
        "TCG-6month-prices": [],
        "TCG-1year-prices": [],
        "TCG-all-prices": [],
    }

    price_history = raw_card.get("price_history", [])
    if not price_history:
        return result

    # Tìm chart phù hợp với variant hiện tại (ưu tiên Raw + variant_name)
    nm_data = []
    for chart in price_history:
        chart_id = chart.get("chart_id", "")
        # Chart ID format: "me3-1_Raw_normal_history"
        # Khớp variant name trong chart_id
        if variant_name.lower() not in chart_id.lower():
            continue

        series_list = chart.get("series", [])
        for series in series_list:
            if series.get("name", "").upper() == "NM":
                nm_data = series.get("data", [])
                break
        if nm_data:
            break

    if not nm_data:
        # Fallback: lấy chart đầu tiên có NM data
        for chart in price_history:
            for series in chart.get("series", []):
                if series.get("name", "").upper() == "NM":
                    nm_data = series.get("data", [])
                    break
            if nm_data:
                break

    if not nm_data:
        return result

    # Convert tất cả raw data thành list of {"date": ..., "price": ...}
    all_points = []
    for point in nm_data:
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            all_points.append({"date": str(point[0]), "price": float(point[1])})

    if not all_points:
        return result

    # TCG-all-prices = toàn bộ data
    result["TCG-all-prices"] = all_points

    # Chia theo time range dựa trên ngày cuối cùng trong data
    try:
        last_date = _dt.strptime(all_points[-1]["date"], "%Y-%m-%d")
    except (ValueError, KeyError):
        return result

    cutoffs = {
        "TCG-1month-prices": last_date - _td(days=30),
        "TCG-3month-prices": last_date - _td(days=90),
        "TCG-6month-prices": last_date - _td(days=180),
        "TCG-1year-prices": last_date - _td(days=365),
    }

    for key, cutoff in cutoffs.items():
        result[key] = [
            p for p in all_points
            if _safe_parse_date(p["date"]) and _dt.strptime(p["date"], "%Y-%m-%d") >= cutoff
        ]

    return result


def _safe_parse_date(date_str: str) -> bool:
    """Kiểm tra xem date string có parse được không."""
    try:
        _dt.strptime(date_str, "%Y-%m-%d")
        return True
    except (ValueError, TypeError):
        return False


def _extract_graded_from_history(raw_card: dict, variant_name: str = "normal") -> list:
    """
    Trích xuất graded price series (PSA, CGC, BGS) từ price_history.

    Scrydex lưu graded prices dưới dạng series riêng trong Chartkick data.
    Series name thường là "PSA 10", "CGC 10", "BGS 9.5", v.v.

    Trả về list of {
        "grader": "PSA",
        "grade": "10",
        "prices": [{"date": "...", "price": ...}]
    }
    """
    price_history = raw_card.get("price_history", [])
    if not price_history:
        return []

    graded = []
    grader_keywords = {"PSA", "CGC", "BGS", "SGC", "ACE"}

    for chart in price_history:
        chart_id = chart.get("chart_id", "")
        if variant_name.lower() not in chart_id.lower():
            continue

        for series in chart.get("series", []):
            name = series.get("name", "").strip()
            # Kiểm tra xem series name có phải graded không
            parts = name.split()
            if len(parts) >= 2 and parts[0].upper() in grader_keywords:
                grader = parts[0].upper()
                grade = parts[1]
                data_points = []
                for point in series.get("data", []):
                    if isinstance(point, (list, tuple)) and len(point) >= 2:
                        data_points.append({
                            "date": str(point[0]),
                            "price": float(point[1])
                        })
                if data_points:
                    graded.append({
                        "grader": grader,
                        "grade": grade,
                        "prices": data_points
                    })

    return graded


def _get_weakness_type(raw_card: dict) -> str:
    """
    Trích xuất weakness type từ raw card data.
    Handle cả trường hợp:
    - weakness là dict: {"type": "fire", "value": "×2"}
    - weakness là string: "fire"
    - weakness là None hoặc rỗng
    """
    weakness = raw_card.get("weakness")
    if weakness is None:
        return ""
    if isinstance(weakness, dict):
        w_type = weakness.get("type")
        return w_type if w_type else ""
    if isinstance(weakness, str):
        return weakness
    return ""


def _get_energy_icon(type_name: str) -> str:
    """Map energy type name → icon URL."""
    return ENERGY_ICONS.get(type_name.lower(), "") if type_name else ""


def _extract_current_price(raw_card: dict) -> str:
    """Trích xuất giá Near Mint từ pricing data."""
    pricing = raw_card.get("pricing", [])
    if not pricing:
        return "$0.00"
    conditions = pricing[0].get("conditions", {})
    nm = conditions.get("Near Mint", {})
    market = nm.get("market", "0")
    if market:
        return f"${market}"
    return "$0.00"


def _transform_attacks(raw_attacks: list) -> list:
    """Chuyển đổi attacks từ format Scrydex → format data mẫu."""
    result = []
    for atk in raw_attacks:
        cost_types = atk.get("cost", [])
        energy_urls = [_get_energy_icon(t) for t in cost_types]

        result.append({
            "name": atk.get("name", ""),
            "damage": atk.get("damage", ""),
            "description": atk.get("text", "") or "No attack description available.",
            "energy": energy_urls,
            "_id": uuid.uuid4().hex[:24],
        })
    return result


def _generate_buy_links(card_name: str, expansion: str, tcg_url: str = "") -> list:
    """Tạo danh sách buy links."""
    search_query = quote_plus(f"{card_name} {expansion}")
    card_query = quote_plus(card_name)

    return [
        {
            "Shop": "Ebay",
            "link": f"https://www.ebay.com/sch/i.html?_nkw={search_query}"
        },
        {
            "Shop": "Cardmarket",
            "link": f"https://www.cardmarket.com/en/Pokemon/Products/Search?searchString={search_query}"
        },
        {
            "Shop": "TCGPlayer",
            "link": tcg_url or f"https://www.tcgplayer.com/search/all/product?q={card_query}"
        },
    ]


def transform_card_for_mongo(raw_card: dict, store_id: str, card_id: str) -> dict:
    """
    Chuyển đổi raw Scrydex card → format data mẫu.
    Document này sẽ được lưu trực tiếp vào MongoDB.
    """
    card_name = raw_card.get("name", "Unknown")
    # Dọn text bẩn từ Scrydex UI (nút "View API" bị dính vào tên thẻ)
    card_name = card_name.replace("View API", "").strip()
    expansion_name = raw_card.get("expansion_name", "")
    types = raw_card.get("types", [])
    energy_type = types[0] if types else ""
    variants = raw_card.get("variants", [])
    current_price = _extract_current_price(raw_card)

    # Trích variant name từ card_id (format: "me3-1_normal")
    variant_name = "normal"
    if "_" in card_id:
        variant_name = card_id.split("_", 1)[1]

    # Parse price history → TCG-*-prices
    price_data = _parse_price_history(raw_card, variant_name)

    # Parse graded prices từ price history
    graded_prices = _extract_graded_from_history(raw_card, variant_name)

    doc = {
        # MongoDB identifiers
        "_id": f"{store_id}:{card_id}",
        "card_id": card_id,
        "store_id": store_id,

        # === Data mẫu fields (match UI production) ===
        "cardName": card_name,
        "cardNameEn": card_name,
        "rarity": raw_card.get("rarity", ""),
        "energy": _get_energy_icon(energy_type),
        "artist": raw_card.get("artist", ""),
        "year": EXPANSION_YEARS.get(expansion_name, "Unknown"),
        "type": raw_card.get("supertype", "Pokemon"),
        "finish": variants[0].get("label", "Normal") if variants else "Normal",
        "seriesExpansion": f"ME: {expansion_name}" if expansion_name else "",
        "seriesExpansionEn": raw_card.get("expansion_series", ""),
        "description": "No description available for this card.",
        "currentPrice": current_price,
        "predictedPrice": current_price,
        "priceLink": f"https://www.tcgplayer.com/search/all/product?q={quote_plus(card_name)}",

        "attacks": _transform_attacks(raw_card.get("attacks", [])),
        "hp": raw_card.get("hp", ""),
        "weakness": _get_weakness_type(raw_card),

        # === TCG Price History (GĐ2) — parsed from Scrydex Chartkick ===
        "TCG-1month-prices": price_data["TCG-1month-prices"],
        "TCG-3month-prices": price_data["TCG-3month-prices"],
        "TCG-6month-prices": price_data["TCG-6month-prices"],
        "TCG-1year-prices": price_data["TCG-1year-prices"],
        "TCG-all-prices": price_data["TCG-all-prices"],

        # === TCG Forecast (GĐ4) ===
        "TCG-1month-forecast-prices": [],
        "TCG-3month-forecast-prices": [],
        "TCG-6month-forecast-prices": [],
        "TCG-1year-forecast-prices": [],
        "TCG-all-forecast-prices": [],

        # === CM Price History (GĐ3) ===
        "CM-1month-prices": [],
        "CM-3month-prices": [],
        "CM-6month-prices": [],
        "CM-1year-prices": [],
        "CM-all-prices": [],

        # === CM Forecast (GĐ4) ===
        "CM-1month-forecast-prices": [],
        "CM-3month-forecast-prices": [],
        "CM-6month-forecast-prices": [],
        "CM-1year-forecast-prices": [],
        "CM-all-forecast-prices": [],

        # === Graded Prices (GĐ3) — parsed from Scrydex Chartkick ===
        "gradedPrices": graded_prices,

        # === Buy Links ===
        "buyLink": _generate_buy_links(card_name, expansion_name),

        # === Internal metadata (không trả ra API) ===
        "_meta": {
            "image_url": variants[0].get("image", "") if variants else "",
            "scrydex_url": raw_card.get("url", ""),
            "printed_number": raw_card.get("printed_number", ""),
            "subtypes": raw_card.get("subtypes", []),
            "retreat_cost": raw_card.get("retreat_cost", []),
            "pricing_raw": raw_card.get("pricing", []),
            "price_history_raw": raw_card.get("price_history", []),
            "sales_stats": raw_card.get("sales_stats", {}),
            "variants_raw": raw_card.get("variants", []),
        }
    }

    return doc

