import logging
import base64
from typing import List, Optional
from google import genai
from google.genai import types

from app.config import GEMINI_API_KEY, GEMINI_EMBEDDING_MODEL, GEMINI_EMBEDDING_DIM

logger = logging.getLogger(__name__)

class GeminiEmbeddingService:
    """
    Service wrapper cho Gemini Embedding API (google-genai SDK).
    Dùng cho cả file search (runtime) và batch migration.
    """
    
    VECTOR_DIM = GEMINI_EMBEDDING_DIM
    MODEL = GEMINI_EMBEDDING_MODEL
    
    def __init__(self, api_key: str = None):
        """
        api_key: nếu truyền vào sẽ dùng key đó thay vì GEMINI_API_KEY mặc định.
                 Hữu ích khi migration dùng key riêng để không ảnh hưởng quota search.
        """
        key = api_key or GEMINI_API_KEY
        if not key:
            logger.warning("GEMINI_API_KEY chưa được cấu hình. GeminiEmbeddingService sẽ không hoạt động.")
            self.client = None
            return
            
        logger.info(f"Khởi tạo Gemini Client cho {self.MODEL} ({self.VECTOR_DIM}-dim)")
        self.client = genai.Client(api_key=key)
        
    def embed_image(self, image_bytes: bytes) -> Optional[List[float]]:
        """
        Embed 1 ảnh duy nhất, trả về vector List[float].
        """
        if not self.client:
            return None
            
        try:
            # SDK yêu cầu Part content. Dùng blob với mime_type.
            image_part = types.Part.from_bytes(
                data=image_bytes,
                mime_type="image/jpeg" # Gemini chấp nhận các định dạng ảnh phổ biến
            )
            
            response = self.client.models.embed_content(
                model=self.MODEL,
                contents=image_part
            )
            
            if response.embeddings and len(response.embeddings) > 0:
                vector = response.embeddings[0].values
                # Đảm bảo vector đúng dimension (768)
                if len(vector) != self.VECTOR_DIM:
                    logger.warning(f"Vector dimension mismatch! Expected {self.VECTOR_DIM}, got {len(vector)}")
                return vector
            else:
                logger.error("Không nhận được embedding từ Gemini API.")
                return None
                
        except Exception as e:
            logger.error(f"Lỗi khi embed ảnh với Gemini: {e}")
            return None
