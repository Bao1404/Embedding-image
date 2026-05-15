"""
Image Downloader — Download ảnh Pokémon card từ Scrydex.

Đọc file JSON đã cào → extract image URLs → download về thư mục local.
Hỗ trợ rate limiting để không bị block.
"""

import os
import json
import asyncio
import httpx
from app.config import DATA_DIR, IMAGES_DIR, DOWNLOAD_DELAY, DOWNLOAD_TIMEOUT


def _make_card_id(card: dict, variant_name: str = "normal") -> str:
    """Tạo ID duy nhất cho 1 thẻ dựa trên URL."""
    url = card.get("url", "")
    # URL dạng: /pokemon/cards/spinarak/me3-1?variant=normal
    # Lấy phần me3-1 làm base ID
    parts = url.split("/")
    slug = parts[-1].split("?")[0] if parts else "unknown"
    return f"{slug}_{variant_name}"


def load_cards_from_json(json_path: str) -> list[dict]:
    """
    Đọc 1 file JSON và trả về list cards với image info.
    
    Returns: List of {
        "card_id": "me3-1_normal",
        "name": "Spinarak",
        "image_url": "https://images.scrydex.com/pokemon/me3-1/medium",
        "metadata": { ... tất cả thông tin thẻ ... }
    }
    """
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    raw_cards = data.get("cards", data) if isinstance(data, dict) else data
    if not isinstance(raw_cards, list):
        return []
    
    result = []
    for card in raw_cards:
        variants = card.get("variants", [])
        if not variants:
            continue
        
        # Lấy variant đầu tiên (thường là normal hoặc holofoil)
        variant = variants[0]
        image_url = variant.get("image", "")
        variant_name = variant.get("name", "normal")
        
        if not image_url:
            continue
        
        card_id = _make_card_id(card, variant_name)
        
        # Trích xuất pricing
        price_nm = ""
        pricing = card.get("pricing", [])
        if pricing:
            conditions = pricing[0].get("conditions", {})
            nm = conditions.get("Near Mint", {})
            market_price = nm.get("market", "")
            if market_price:
                price_nm = f"${market_price}"
        
        # Gộp types thành string
        types = ", ".join(card.get("types", [])) if card.get("types") else ""
        
        result.append({
            "card_id": card_id,
            "name": card.get("name", "Unknown"),
            "image_url": image_url,
            "metadata": {
                "name": card.get("name", "Unknown"),
                "expansion": card.get("expansion_name", ""),
                "number": card.get("printed_number", ""),
                "rarity": card.get("rarity", ""),
                "hp": card.get("hp", ""),
                "types": types,
                "artist": card.get("artist", ""),
                "supertype": card.get("supertype", ""),
                "variant": variant_name,
                "image_url": image_url,
                "scrydex_url": card.get("url", ""),
                "price_nm": price_nm,
            }
        })
    
    return result


def find_json_files(data_dir: str, filter_name: str = None) -> list[str]:
    """Tìm tất cả file JSON data trong thư mục."""
    files = []
    for f in os.listdir(data_dir):
        if not f.endswith(".json"):
            continue
        if f == "config.json":
            continue
        if filter_name and filter_name not in f:
            continue
        files.append(os.path.join(data_dir, f))
    return sorted(files)


async def download_images(cards: list[dict], output_dir: str, store_id: str = None) -> dict:
    """
    Download ảnh cho list cards.
    
    Nếu store_id được cung cấp, ảnh sẽ nằm trong subfolder: output_dir/store_id/
    
    Returns: {"downloaded": int, "skipped": int, "errors": int, "paths": {card_id: path}}
    """
    # Tạo subfolder theo store_id nếu có
    if store_id:
        actual_dir = os.path.join(output_dir, store_id)
    else:
        actual_dir = output_dir
    os.makedirs(actual_dir, exist_ok=True)
    
    stats = {"downloaded": 0, "skipped": 0, "errors": 0, "paths": {}}
    
    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT) as client:
        for i, card in enumerate(cards):
            card_id = card["card_id"]
            image_url = card["image_url"]
            
            # Tên file local
            safe_name = card_id.replace("/", "_").replace("\\", "_")
            local_path = os.path.join(actual_dir, f"{safe_name}.jpg")
            
            # Bỏ qua nếu đã download
            if os.path.exists(local_path) and os.path.getsize(local_path) > 1000:
                stats["skipped"] += 1
                stats["paths"][card_id] = local_path
                continue
            
            try:
                resp = await client.get(image_url)
                resp.raise_for_status()
                
                with open(local_path, "wb") as f:
                    f.write(resp.content)
                
                stats["downloaded"] += 1
                stats["paths"][card_id] = local_path
                
                if (i + 1) % 10 == 0:
                    print(f"  📥 Downloaded {i+1}/{len(cards)}")
                
                # Rate limit
                await asyncio.sleep(DOWNLOAD_DELAY)
                
            except Exception as e:
                print(f"  ❌ Error downloading {card_id}: {e}")
                stats["errors"] += 1
    
    return stats
