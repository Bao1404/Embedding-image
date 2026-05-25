from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient
import ssl
import logging

from app.config import MONGO_URI, MONGO_DB_NAME

logger = logging.getLogger(__name__)

# SSL context cho MongoDB Atlas (fix OpenSSL 3.0.x compatibility)
def _get_ssl_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

# Async client cho FastAPI
_async_client = None

def get_async_db():
    """Trả về async database instance (dùng trong FastAPI endpoint)."""
    global _async_client
    if not MONGO_URI:
        return None
    if not _async_client:
        _async_client = AsyncIOMotorClient(MONGO_URI, tls=True, tlsAllowInvalidCertificates=True)
        logger.info(f"Connected to MongoDB (Async): {MONGO_DB_NAME}")
    return _async_client[MONGO_DB_NAME]

def close_async_db():
    """Đóng kết nối async."""
    global _async_client
    if _async_client:
        _async_client.close()
        _async_client = None
        logger.info("Closed MongoDB (Async) connection")

# Sync client cho các background script
def get_sync_db():
    """Trả về sync database instance (dùng trong scraper và background scripts)."""
    if not MONGO_URI:
        return None
    client = MongoClient(MONGO_URI, tls=True, tlsAllowInvalidCertificates=True)
    return client[MONGO_DB_NAME]

