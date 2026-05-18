import os
import sys
import logging
import datetime

# Ensure we can import app config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.qdrant_service import QdrantSearchService

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

def main():
    try:
        logging.info("Starting Qdrant Cloud heartbeat ping...")
        qdrant_svc = QdrantSearchService()
        if not qdrant_svc.client:
            logging.error("Failed to initialize Qdrant client for heartbeat. Check .env configuration.")
            return

        info = qdrant_svc.get_collection_info()
        if info:
            logging.info(f"Heartbeat successful. Collection status: {info.get('status')}, Points count: {info.get('points_count')}")
        else:
            logging.warning("Heartbeat completed, but could not retrieve collection info (collection might not exist yet).")
    except Exception as e:
        logging.error(f"Heartbeat failed with error: {e}")

if __name__ == "__main__":
    main()
