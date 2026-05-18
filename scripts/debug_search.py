"""Debug: test search trực tiếp để xem Qdrant trả về gì."""
import sys, os, glob
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from app.embedding_service import GeminiEmbeddingService
from app.qdrant_service import QdrantSearchService

svc = GeminiEmbeddingService()
qdrant = QdrantSearchService()

# Lấy 1 ảnh test từ ascended-heroes
imgs = glob.glob("d:/LimGrow/Embedding-image/images/ascended-heroes/*.jpg")[:1]
if not imgs:
    print("Không tìm thấy ảnh test!")
    sys.exit(1)

print(f"Test image: {imgs[0]}")

with open(imgs[0], "rb") as f:
    vec = svc.embed_image(f.read())

if not vec:
    print("Embed thất bại!")
    sys.exit(1)

print(f"Vector dim: {len(vec)}")

# Dùng wrapper search (giống API endpoint)
results = qdrant.search(vec, top_k=5)
print(f"\nWrapper results: {len(results)}")
for r in results:
    print(f"  {r.get('store_id')}:{r.get('card_id')} score={r.get('score',0):.4f}")

# Check collection info
info = qdrant.client.get_collection(qdrant.collection_name)
print(f"\nCollection vector size: {info.config.params.vectors}")
print(f"Points count: {info.points_count}")

# Check 1 point mẫu vector dim
sample = qdrant.client.scroll(collection_name=qdrant.collection_name, limit=1, with_vectors=True)[0]
if sample:
    print(f"Sample point vector dim: {len(sample[0].vector)}")
