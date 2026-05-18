import base64
import logging
from typing import List, Dict, Any, Optional

from qdrant_client import QdrantClient
from qdrant_client.http import models

from app.config import (
    QDRANT_URL,
    QDRANT_API_KEY,
    QDRANT_COLLECTION_NAME,
    GEMINI_EMBEDDING_DIM,
)

logger = logging.getLogger(__name__)

class QdrantSearchService:
    """
    Quản lý Qdrant Cloud collection cho Pokémon card image search.
    Sử dụng vector được embed từ external provider (vd: Gemini Embedding-2).
    """
    
    VECTOR_SIZE = GEMINI_EMBEDDING_DIM  # 3072 for Gemini Embedding-2
    
    def __init__(self):
        if not QDRANT_URL or not QDRANT_API_KEY:
            logger.warning("QDRANT_URL hoặc QDRANT_API_KEY chưa được cấu hình. QdrantSearchService sẽ không hoạt động.")
            self.client = None
            return

        logger.info(f"Khởi tạo QdrantClient kết nối tới {QDRANT_URL}")
        self.client = QdrantClient(
            url=QDRANT_URL,
            api_key=QDRANT_API_KEY,
            timeout=30,
        )
        self.collection_name = QDRANT_COLLECTION_NAME
        self._ensure_collection()
    
    def _ensure_collection(self):
        """Tạo collection nếu chưa có."""
        if not self.client: return
        try:
            collections_response = self.client.get_collections()
            collection_names = [c.name for c in collections_response.collections]
            
            if self.collection_name not in collection_names:
                logger.info(f"Tạo mới collection '{self.collection_name}' với vector size {self.VECTOR_SIZE}")
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=models.VectorParams(
                        size=self.VECTOR_SIZE,
                        distance=models.Distance.COSINE
                    )
                )
            else:
                logger.info(f"Collection '{self.collection_name}' đã tồn tại.")
        except Exception as e:
            logger.error(f"Lỗi khi kiểm tra/tạo collection Qdrant: {e}")
            raise
    
    def upsert_card(self, point_id: str, vector: List[float], payload: Dict[str, Any]):
        """
        Upsert 1 card vào collection.
        - point_id: uuid5 hash từ global_id
        - vector: vector 768-dim từ Gemini
        - payload: metadata dict (card_id, store_id, name, ...)
        """
        if not self.client: return
        
        self.client.upsert(
            collection_name=self.collection_name,
            points=[
                models.PointStruct(
                    id=point_id,
                    vector=vector,
                    payload=payload,
                )
            ],
        )
    
    def upsert_batch(self, points_data: List[tuple], batch_size: int = 64):
        """
        Upsert nhiều cards cùng lúc.
        points_data: list of (point_id, vector, payload)
        """
        if not self.client: return
        
        total = len(points_data)
        logger.info(f"Bắt đầu upsert {total} points vào {self.collection_name}...")
        
        for i in range(0, total, batch_size):
            batch = points_data[i:i + batch_size]
            
            points = [
                models.PointStruct(
                    id=point_id,
                    vector=vector,
                    payload=payload,
                )
                for point_id, vector, payload in batch
            ]
            
            self.client.upsert(
                collection_name=self.collection_name,
                points=points
            )
            logger.info(f"Đã upsert batch {i // batch_size + 1} ({len(batch)} points)")

    def search(self, query_vector: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Tìm card giống nhất bằng vector.
        Returns: list of {card_id, store_id, score, name, ...}
        """
        if not self.client: return []
        
        try:
            # qdrant-client >= 1.18 dùng query_points() thay vì search()
            results = self.client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                with_payload=True,
                limit=top_k,
            )
            
            return [
                {
                    "card_id": point.payload.get("card_id") if point.payload else None,
                    "store_id": point.payload.get("store_id") if point.payload else None,
                    "score": point.score,
                    "name": point.payload.get("name") if point.payload else None,
                    "global_id": point.payload.get("global_id") if point.payload else None,
                }
                for point in results.points
            ]
        except Exception as e:
            logger.error(f"Lỗi khi search bằng Qdrant: {e}")
            return []
    
    def get_collection_info(self) -> Optional[Dict[str, Any]]:
        """Lấy thống kê collection (count, status)."""
        if not self.client: return None
        try:
            info = self.client.get_collection(self.collection_name)
            return {
                "status": str(info.status),
                "points_count": info.points_count,
                "vectors_count": getattr(info, "vectors_count", info.points_count),
            }
        except Exception as e:
            logger.error(f"Lỗi khi lấy info collection: {e}")
            return None
    
    def delete_collection(self):
        """Xóa collection (dùng khi cần rebuild)."""
        if not self.client: return
        logger.warning(f"Đang xóa collection '{self.collection_name}'...")
        self.client.delete_collection(self.collection_name)
    
    def health_check(self):
        """Ping cluster để giữ active (heartbeat)."""
        if not self.client: return False
        try:
            collections = self.client.get_collections()
            return True
        except Exception:
            return False
