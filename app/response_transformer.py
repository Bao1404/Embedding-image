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
    Parse price_history raw từ Chartkick data → TCG-all-prices.

    Trả về dict với key: TCG-all-prices
    Value là list of {"date": "YYYY-MM-DD", "price": float}.
    """
    result = {
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
        if isinstance(point, (list, tuple)) and len(point) >= 2 and point[1] is not None:
            try:
                all_points.append({"date": str(point[0]), "price": float(point[1])})
            except (ValueError, TypeError):
                continue

    if not all_points:
        return result

    # TCG-all-prices = toàn bộ data
    result["TCG-all-prices"] = all_points

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
                    if isinstance(point, (list, tuple)) and len(point) >= 2 and point[1] is not None:
                        try:
                            data_points.append({
                                "date": str(point[0]),
                                "price": float(point[1])
                            })
                        except (ValueError, TypeError):
                            continue
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


def _get_card_image(variants: list, variant_name: str) -> str:
    """Lấy card image URL từ variant matching tên variant hiện tại."""
    if not variants:
        return ""
    # Tìm variant khớp tên
    for v in variants:
        if v.get("name", "") == variant_name and v.get("image"):
            return v["image"]
    # Fallback: lấy variant đầu tiên có image
    for v in variants:
        if v.get("image"):
            return v["image"]
    return ""


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

        # === Card Image ===
        "cardImage": _get_card_image(variants, variant_name),

        "attacks": _transform_attacks(raw_card.get("attacks", [])),
        "hp": raw_card.get("hp", ""),
        "weakness": _get_weakness_type(raw_card),

        # === TCG Price History — only "all" stored in DB ===
        "TCG-all-prices": price_data["TCG-all-prices"],

        # === TCG Forecast — only "all" stored in DB ===
        "TCG-all-forecast-prices": [],

        # === CM Price History — only "all" stored in DB ===
        "CM-all-prices": [],

        # === CM Forecast — only "all" stored in DB ===
        "CM-all-forecast-prices": [],

        # === Graded Prices (GĐ3) — parsed from Scrydex Chartkick ===
        "gradedPrices": graded_prices,

        # === Buy Links ===
        "buyLink": _generate_buy_links(card_name, expansion_name),
    }

    return doc


# ═══════════════════════════════════════════
# API RESPONSE EXPANSION
# ═══════════════════════════════════════════

def _slice_by_days(all_points: list, days: int) -> list:
    """Slice price history to only include points within the last N days."""
    if not all_points:
        return []

    # Determine the date of the last data point
    last_point = all_points[-1]
    if isinstance(last_point, dict):
        last_date_str = last_point.get("date", "")
    elif isinstance(last_point, (list, tuple)):
        last_date_str = last_point[0] if last_point else ""
    else:
        return all_points

    try:
        last_date = _dt.strptime(last_date_str, "%Y-%m-%d")
    except (ValueError, TypeError):
        return all_points

    cutoff = last_date - _td(days=days)

    result = []
    for p in all_points:
        if isinstance(p, dict):
            date_str = p.get("date", "")
        elif isinstance(p, (list, tuple)):
            date_str = p[0] if p else ""
        else:
            continue

        try:
            point_date = _dt.strptime(date_str, "%Y-%m-%d")
            if point_date >= cutoff:
                result.append(p)
        except (ValueError, TypeError):
            continue

    return result


def _slice_forecast_by_weeks(forecast_points: list, max_weeks: int) -> list:
    """Slice forecast array to only include up to max_weeks data points."""
    if not forecast_points:
        return []
    return forecast_points[:max_weeks]


def expand_price_fields_for_api(card: dict) -> dict:
    """
    Expand the 4 stored 'all' arrays into the 20 arrays the frontend expects.

    DB stores only:
      TCG-all-prices, TCG-all-forecast-prices,
      CM-all-prices, CM-all-forecast-prices

    API returns all 20 fields including 1month, 3month, 6month, 1year slices.
    """
    if not card:
        return card

    # Time range definitions: (field_prefix, days_back, forecast_weeks)
    TIME_RANGES = {
        "1month": (30, 4),
        "3month": (90, 12),
        "6month": (180, 26),
        "1year": (365, 52),
    }

    for source in ("TCG", "CM"):
        all_prices = card.get(f"{source}-all-prices", [])
        all_forecast = card.get(f"{source}-all-forecast-prices", [])

        for range_name, (days, weeks) in TIME_RANGES.items():
            # Slice price history
            card[f"{source}-{range_name}-prices"] = _slice_by_days(all_prices, days)
            # Slice forecast
            card[f"{source}-{range_name}-forecast-prices"] = _slice_forecast_by_weeks(all_forecast, weeks)

    return card

