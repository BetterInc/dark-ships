"""SAR chip object storage on the same S3-compatible bucket as the cold tier
(MinIO locally, Cloudflare R2 in prod - only endpoint/keys differ, see
docker-compose.yml / DEPLOY.md).

boto3 is blocking, so the helpers hop to a worker thread. The bucket stays
private; the API proxies chips out via /api/position-checks/{id}/chip."""

import asyncio
import functools
import logging

from ..config import get_settings

logger = logging.getLogger(__name__)

CHIP_PREFIX = "sar-chips"


@functools.lru_cache(maxsize=1)
def _client():
    import boto3

    s = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint,
        region_name=s.s3_region if s.s3_region != "auto" else None,
        aws_access_key_id=s.s3_access_key_id,
        aws_secret_access_key=s.s3_secret_access_key,
    )


def chip_object_key(check_id: int) -> str:
    return f"{CHIP_PREFIX}/{check_id}.png"


async def put_chip(check_id: int, png: bytes) -> str | None:
    """Store a chip PNG; returns the object key, or None when storage is
    disabled/unavailable (detection results are still kept in Postgres)."""
    s = get_settings()
    if not s.cold_storage_enabled:
        return None
    key = chip_object_key(check_id)
    try:
        await asyncio.to_thread(
            _client().put_object, Bucket=s.s3_bucket, Key=key,
            Body=png, ContentType="image/png")
        return key
    except Exception:
        logger.exception("Chip upload failed (%s)", key)
        return None


async def get_chip(key: str) -> bytes | None:
    s = get_settings()
    if not s.cold_storage_enabled:
        return None
    try:
        obj = await asyncio.to_thread(
            _client().get_object, Bucket=s.s3_bucket, Key=key)
        return await asyncio.to_thread(obj["Body"].read)
    except Exception:
        logger.warning("Chip fetch failed (%s)", key)
        return None
