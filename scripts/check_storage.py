import os
import sys
from dotenv import load_dotenv
from google import genai

# Load bien moi truong tu file .env. Chu y path gio la ../.env
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(dotenv_path=env_path)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("Loi: Khong tim thay GEMINI_API_KEY trong file .env")
    sys.exit(1)

def check_gemini_storage():
    print("Dang ket noi toi Google Gemini...")
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)

        # ==========================================
        # 1. FILE SEARCH STORE (du lieu vinh vien)
        # Gioi han Free Tier: 1 GB / project
        # ==========================================
        stores = list(client.file_search_stores.list())
        store_docs = {}
        store_total_bytes = 0

        for store in stores:
            doc_count = 0
            store_bytes = 0
            try:
                for doc in client.file_search_stores.documents.list(parent=store.name):
                    doc_count += 1
                    if hasattr(doc, 'size_bytes') and doc.size_bytes:
                        store_bytes += doc.size_bytes
            except Exception:
                pass
            store_docs[store.name] = {
                "display_name": getattr(store, 'display_name', 'N/A'),
                "doc_count": doc_count,
                "size_bytes": store_bytes,
            }
            store_total_bytes += store_bytes

        # ==========================================
        # 2. FILES API (du lieu tam, tu xoa sau 48h)
        # Gioi han Free Tier: 20 GB / project
        # ==========================================
        files_count = 0
        files_bytes = 0
        for f in client.files.list():
            files_count += 1
            if f.size_bytes:
                files_bytes += f.size_bytes

        # ==========================================
        # BAO CAO
        # ==========================================
        store_mb = store_total_bytes / (1024 * 1024) if store_total_bytes else 0
        files_mb = files_bytes / (1024 * 1024) if files_bytes else 0

        # File Search Store limit = 1 GB
        store_limit_mb = 1024.0
        store_percent = (store_mb / store_limit_mb) * 100 if store_limit_mb else 0

        # Files API limit = 20 GB
        files_limit_mb = 20 * 1024.0
        files_percent = (files_mb / files_limit_mb) * 100 if files_limit_mb else 0

        print("")
        print("=" * 55)
        print("  BAO CAO DUNG LUONG LUU TRU GEMINI API")
        print("=" * 55)

        print("")
        print("--- FILE SEARCH STORE (luu vinh vien) ---")
        print(f"  So Store:        {len(stores)}")
        total_docs = sum(s["doc_count"] for s in store_docs.values())
        print(f"  Tong documents:  {total_docs}")
        print(f"  Dung luong:      {store_mb:.2f} MB / {store_limit_mb:.0f} MB (1 GB)")
        print(f"  Ti le su dung:   {store_percent:.2f}%")

        if stores:
            print("")
            print("  Chi tiet tung Store:")
            for name, info in store_docs.items():
                s_mb = info["size_bytes"] / (1024 * 1024) if info["size_bytes"] else 0
                print(f"    - {info['display_name']} ({name})")
                print(f"      Documents: {info['doc_count']} | Size: {s_mb:.2f} MB")

        print("")
        print("--- FILES API (tu xoa sau 48h) ---")
        print(f"  So file:         {files_count}")
        print(f"  Dung luong:      {files_mb:.2f} MB / {files_limit_mb:.0f} MB (20 GB)")
        print(f"  Ti le su dung:   {files_percent:.4f}%")

        print("")
        print("=" * 55)
        print("  Luu y:")
        print("  - File Search Store: Data luu VINH VIEN, limit 1 GB")
        print("  - Files API: Data TU XOA sau 48h, limit 20 GB")
        print("  - Khong hien thi tren Google Drive hay Cloud Storage")
        print("=" * 55)

    except Exception as e:
        print(f"\nLoi khi lay thong tin: {e}")

if __name__ == "__main__":
    check_gemini_storage()
