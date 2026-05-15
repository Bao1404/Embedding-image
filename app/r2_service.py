"""
Cloudflare R2 Storage Service — Lưu trữ ảnh thẻ bài khi search trả về null.

Sử dụng boto3 (S3-compatible SDK) để kết nối Cloudflare R2.
Ref: https://developers.cloudflare.com/r2/examples/aws/boto3/
"""

import io
import json
import uuid
import logging
from datetime import datetime, timezone, timedelta

import boto3
from botocore.exceptions import ClientError

from app.config import (
    R2_ACCOUNT_ID,
    R2_ACCESS_KEY_ID,
    R2_SECRET_ACCESS_KEY,
    R2_BUCKET_NAME,
    R2_ENDPOINT_URL,
)

logger = logging.getLogger(__name__)

# Timezone Việt Nam
VN_TZ = timezone(timedelta(hours=7))


class R2StorageService:
    """
    Cloudflare R2 — lưu ảnh thẻ bài khi Gemini search không match.

    Flow:
    1. Search trả về match=False → upload_unknown_card()
    2. Admin review → list_unknown_cards()
    3. Xem ảnh → get_unknown_card_url()
    4. Đã xử lý → delete_unknown_card()
    """

    def __init__(self):
        """Khởi tạo boto3 client. Nếu thiếu credentials → disabled."""
        self.enabled = False
        self.client = None

        if not all([R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY]):
            logger.warning("⚠️ R2 credentials chưa cấu hình — unknown cards sẽ không được lưu")
            return

        try:
            self.client = boto3.client(
                service_name="s3",
                endpoint_url=R2_ENDPOINT_URL,
                aws_access_key_id=R2_ACCESS_KEY_ID,
                aws_secret_access_key=R2_SECRET_ACCESS_KEY,
                region_name="auto",  # Required by SDK, not used by R2
            )
            # Quick health check — head bucket
            self.client.head_bucket(Bucket=R2_BUCKET_NAME)
            self.enabled = True
            logger.info(f"✅ R2 connected: bucket={R2_BUCKET_NAME}")
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "404":
                logger.error(f"❌ R2 bucket '{R2_BUCKET_NAME}' không tồn tại!")
            elif error_code == "403":
                logger.error(f"❌ R2 credentials không có quyền truy cập bucket '{R2_BUCKET_NAME}'")
            else:
                logger.error(f"❌ R2 connection failed: {e}")
        except Exception as e:
            logger.error(f"❌ R2 init error: {e}")

    def is_configured(self) -> bool:
        """R2 có sẵn sàng không?"""
        return self.enabled and self.client is not None

    def upload_unknown_card(
        self,
        image_bytes: bytes,
        filename: str = "unknown.jpg",
        gemini_result: dict = None,
    ) -> str:
        """
        Upload ảnh unknown + metadata JSON lên R2.

        Args:
            image_bytes: Nội dung ảnh (bytes)
            filename: Tên file gốc từ người dùng
            gemini_result: Dict kết quả từ gemini_svc.search()

        Returns:
            Object key (vd: "unknown/2026-05-14T08-35-10_a1b2c3d4")
        """
        if not self.is_configured():
            raise RuntimeError("R2 chưa cấu hình")

        # Tạo key unique: timestamp + short uuid
        now = datetime.now(VN_TZ)
        timestamp = now.strftime("%Y-%m-%dT%H-%M-%S")
        short_id = uuid.uuid4().hex[:8]
        base_key = f"unknown/{timestamp}_{short_id}"

        # Xác định content type
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
        content_type_map = {
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
            "webp": "image/webp",
        }
        content_type = content_type_map.get(ext, "image/jpeg")
        image_key = f"{base_key}.{ext}"

        # 1. Upload ảnh
        self.client.upload_fileobj(
            Fileobj=io.BytesIO(image_bytes),
            Bucket=R2_BUCKET_NAME,
            Key=image_key,
            ExtraArgs={"ContentType": content_type},
        )

        # 2. Upload metadata JSON
        metadata = {
            "uploaded_at": now.isoformat(),
            "original_filename": filename,
            "content_type": content_type,
            "size_bytes": len(image_bytes),
            "gemini_result": gemini_result or {},
            "status": "pending",
        }
        meta_key = f"{base_key}.json"
        self.client.put_object(
            Bucket=R2_BUCKET_NAME,
            Key=meta_key,
            Body=json.dumps(metadata, ensure_ascii=False, indent=2),
            ContentType="application/json",
        )

        logger.info(f"☁️ R2 uploaded: {image_key} ({len(image_bytes)} bytes)")
        return base_key

    def list_unknown_cards(self, limit: int = 50) -> dict:
        """
        Liệt kê ảnh unknown trên R2.

        Returns:
            {"total": N, "cards": [{key, uploaded_at, original_filename, size_bytes}, ...]}
        """
        if not self.is_configured():
            return {"total": 0, "cards": []}

        try:
            response = self.client.list_objects_v2(
                Bucket=R2_BUCKET_NAME,
                Prefix="unknown/",
                MaxKeys=limit * 2,  # x2 vì mỗi card có .jpg + .json
            )

            cards = []
            contents = response.get("Contents", [])

            # Lọc chỉ lấy file ảnh (không lấy .json)
            image_files = [
                obj for obj in contents
                if not obj["Key"].endswith(".json")
            ]

            for obj in image_files:
                key = obj["Key"]
                # Thử đọc metadata JSON tương ứng
                base = key.rsplit(".", 1)[0]  # bỏ extension
                meta = self._read_metadata(f"{base}.json")

                cards.append({
                    "key": key.replace("unknown/", ""),  # chỉ trả phần filename
                    "uploaded_at": meta.get("uploaded_at", obj["LastModified"].isoformat()),
                    "original_filename": meta.get("original_filename", ""),
                    "size_bytes": obj["Size"],
                    "status": meta.get("status", "pending"),
                })

            return {"total": len(cards), "cards": cards}

        except Exception as e:
            logger.error(f"R2 list failed: {e}")
            return {"total": 0, "cards": [], "error": str(e)}

    def _read_metadata(self, json_key: str) -> dict:
        """Đọc metadata JSON từ R2 (internal helper)."""
        try:
            response = self.client.get_object(
                Bucket=R2_BUCKET_NAME,
                Key=json_key,
            )
            return json.loads(response["Body"].read().decode("utf-8"))
        except Exception:
            return {}

    def get_unknown_card_url(self, key: str) -> str:
        """
        Tạo presigned URL để xem ảnh (valid 1 giờ).

        Args:
            key: Object key đầy đủ (vd: "unknown/2026-05-14T08-35-10_a1b2c3d4.jpg")
        """
        if not self.is_configured():
            raise RuntimeError("R2 chưa cấu hình")

        url = self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": R2_BUCKET_NAME, "Key": key},
            ExpiresIn=3600,  # 1 hour
        )
        return url

    def delete_unknown_card(self, key: str) -> bool:
        """
        Xóa ảnh + metadata JSON trên R2.

        Args:
            key: Object key đầy đủ (vd: "unknown/2026-05-14T08-35-10_a1b2c3d4.jpg")
        """
        if not self.is_configured():
            return False

        try:
            # Xóa ảnh
            self.client.delete_object(Bucket=R2_BUCKET_NAME, Key=key)

            # Xóa metadata JSON tương ứng
            base = key.rsplit(".", 1)[0]
            self.client.delete_object(Bucket=R2_BUCKET_NAME, Key=f"{base}.json")

            logger.info(f"🗑️ R2 deleted: {key}")
            return True
        except Exception as e:
            logger.error(f"R2 delete failed: {e}")
            return False

    def get_stats(self) -> dict:
        """Thống kê R2 bucket."""
        if not self.is_configured():
            return {"enabled": False, "reason": "R2 chưa cấu hình"}

        try:
            response = self.client.list_objects_v2(
                Bucket=R2_BUCKET_NAME,
                Prefix="unknown/",
            )
            contents = response.get("Contents", [])
            image_files = [o for o in contents if not o["Key"].endswith(".json")]
            total_size = sum(o["Size"] for o in contents)

            return {
                "enabled": True,
                "bucket": R2_BUCKET_NAME,
                "unknown_cards": len(image_files),
                "total_objects": len(contents),
                "total_size_bytes": total_size,
                "total_size_mb": round(total_size / (1024 * 1024), 2),
            }
        except Exception as e:
            return {"enabled": True, "error": str(e)}
