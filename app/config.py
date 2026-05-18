"""
Cấu hình tập trung cho toàn bộ project.
Tất cả đường dẫn, model name, threshold đều khai báo ở đây.
"""
import os
from dotenv import load_dotenv

# Load .env file
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

# ═══════════════════════════════════════════
# ĐƯỜNG DẪN
# ═══════════════════════════════════════════

# Thư mục gốc project
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Thư mục chứa các file JSON data đã cào
DATA_DIR = os.path.join(BASE_DIR, "data")

# Thư mục lưu ảnh download về
IMAGES_DIR = os.path.join(BASE_DIR, "images")

# ═══════════════════════════════════════════
# LOCAL CARD STORE
# ═══════════════════════════════════════════

# Pattern để match JSON data files (exclude config files)
JSON_DATA_PATTERN = "*.json"

# ═══════════════════════════════════════════
# SEARCH
# ═══════════════════════════════════════════

# Ngưỡng cosine similarity để coi là "match"
MATCH_THRESHOLD = 0.92

# Số kết quả trả về mặc định
DEFAULT_TOP_K = 5

# ═══════════════════════════════════════════
# DOWNLOAD
# ═══════════════════════════════════════════

# Rate limit khi download ảnh (giây giữa các request)
DOWNLOAD_DELAY = 0.3

# Timeout cho mỗi request download (giây)
DOWNLOAD_TIMEOUT = 15

# ═══════════════════════════════════════════
# GEMINI EMBEDDING
# ═══════════════════════════════════════════

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# Key riêng cho migration (batch upload) — nếu để trống sẽ dùng GEMINI_API_KEY
GEMINI_API_KEY_MIGRATION = os.getenv("GEMINI_API_KEY_MIGRATION", "") or GEMINI_API_KEY
GEMINI_EMBEDDING_MODEL = "gemini-embedding-2"
GEMINI_EMBEDDING_DIM = 3072

# ═══════════════════════════════════════════
# QDRANT CLOUD (Vector Search)
# ═══════════════════════════════════════════

QDRANT_URL = os.getenv("QDRANT_URL", "")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "")
QDRANT_COLLECTION_NAME = os.getenv("QDRANT_COLLECTION_NAME", "pokemon-cards-gemini")

# CLIP cosine similarity >= 0.70 = match
QDRANT_MATCH_THRESHOLD = 0.70
# Cosine >= 0.30 = có thể là Pokémon card (lưu R2 nếu không match)
QDRANT_POKEMON_THRESHOLD = 0.30
QDRANT_TOP_K = 5

# ═══════════════════════════════════════════
# CLOUDFLARE R2 (Unknown Cards Storage)
# ═══════════════════════════════════════════
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME", "pokemon-unknown-cards")
R2_ENDPOINT_URL = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com" if R2_ACCOUNT_ID else ""
