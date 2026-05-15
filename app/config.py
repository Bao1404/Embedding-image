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
# GEMINI FILE SEARCH (Cloud-based)
# ═══════════════════════════════════════════

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_EMBEDDING_MODEL = "models/gemini-embedding-2"
GEMINI_GENERATION_MODEL = "gemini-2.5-flash"
GEMINI_STORE_DISPLAY_NAME = "pokemon-cards"
GEMINI_STORE_CACHE_FILE = os.path.join(BASE_DIR, "gemini_store.json")

# ═══════════════════════════════════════════
# CLOUDFLARE R2 (Unknown Cards Storage)
# ═══════════════════════════════════════════
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME", "pokemon-unknown-cards")
R2_ENDPOINT_URL = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com" if R2_ACCOUNT_ID else ""
