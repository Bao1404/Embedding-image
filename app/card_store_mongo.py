from app.database import get_async_db
import logging

logger = logging.getLogger(__name__)

class MongoCardStore:
    def __init__(self):
        self.db = get_async_db()
        if self.db is not None:
            self.cards = self.db["cards"]
            self.expansions = self.db["expansions"]
        else:
            self.cards = None
            self.expansions = None
            logger.warning("MongoCardStore init without MONGO_URI")

    async def get(self, card_id: str, store_id: str = None) -> dict | None:
        """Lấy 1 card theo card_id và/hoặc store_id. Dữ liệu đã là format chuẩn."""
        if self.cards is None: return None
        
        # Nếu có store_id, tìm theo global _id cho nhanh nhất (O(1) lookup)
        if store_id:
            return await self.cards.find_one({"_id": f"{store_id}:{card_id}"})
            
        # Nếu không có store_id (hiếm khi xảy ra), tìm theo card_id
        return await self.cards.find_one({"card_id": card_id})

    async def list_cards(self, offset=0, limit=20, store_id=None) -> tuple[list, int]:
        """Lấy danh sách cards."""
        if self.cards is None: return [], 0
        
        query = {"store_id": store_id} if store_id else {}
        total = await self.cards.count_documents(query)
        cursor = self.cards.find(query).skip(offset).limit(limit)
        cards = await cursor.to_list(length=limit)
        return cards, total

    async def get_stats(self) -> dict:
        """Lấy thống kê."""
        if self.cards is None: return {"total_cards": 0, "expansions": []}
        
        total = await self.cards.count_documents({})
        expansions = await self.expansions.find().to_list(length=100)
        
        # Format lại kết quả
        formatted_exps = {}
        for exp in expansions:
            formatted_exps[exp["store_id"]] = {
                "set_code": exp.get("set_code"),
                "total_cards": exp.get("total_cards"),
                "url": exp.get("url")
            }
            
        return {
            "total_cards": total,
            "expansions": formatted_exps
        }
