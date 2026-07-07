"""SkyTruth Cerulean - oil slick detections from free Sentinel-1 analysis.

Public OGC API. We seed oil_slicks with recent detections inside our hotspot
regions and refresh daily. A slick at the location of a suspected ship-to-ship
transfer is near-conclusive evidence, even with zero AIS data.
"""

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..config import get_settings
from ..db import SessionLocal
from ..models import OilSlick

logger = logging.getLogger(__name__)

ITEMS_URL = "https://api.cerulean.skytruth.org/collections/public.slick_plus/items"
PAGE_LIMIT = 500
MAX_PAGES_PER_REGION = 6
BACKFILL_DAYS = 90
REFRESH_DAYS = 7
MIN_CONFIDENCE = 0.5


def _centroid(geometry: dict) -> tuple[float, float] | None:
    """Cheap centroid: mean of the outer ring of the first polygon."""
    try:
        coords = geometry["coordinates"][0][0]
        lons = [c[0] for c in coords]
        lats = [c[1] for c in coords]
        return sum(lats) / len(lats), sum(lons) / len(lons)
    except (KeyError, IndexError, ZeroDivisionError, TypeError):
        return None


async def fetch_slicks(bbox: list[list[float]], start: datetime, end: datetime) -> list[dict]:
    (lat_min, lon_min), (lat_max, lon_max) = bbox
    params = {
        "bbox": f"{lon_min},{lat_min},{lon_max},{lat_max}",
        "datetime": f"{start:%Y-%m-%dT%H:%M:%SZ}/{end:%Y-%m-%dT%H:%M:%SZ}",
        "limit": str(PAGE_LIMIT),
    }
    slicks = []
    async with httpx.AsyncClient(timeout=60) as client:
        offset = 0
        for _ in range(MAX_PAGES_PER_REGION):
            resp = await client.get(ITEMS_URL, params={**params, "offset": str(offset)})
            resp.raise_for_status()
            features = resp.json().get("features", [])
            for f in features:
                props = f.get("properties", {})
                confidence = props.get("machine_confidence")
                if confidence is not None and confidence < MIN_CONFIDENCE:
                    continue
                center = _centroid(f.get("geometry", {}))
                raw_ts = props.get("slick_timestamp")
                if center is None or not raw_ts:
                    continue
                ts = datetime.fromisoformat(raw_ts)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                slicks.append({
                    "id": props.get("id") or f.get("id"),
                    "ts": ts, "lat": center[0], "lon": center[1],
                    "area_m2": props.get("area"), "length_m": props.get("length"),
                    "confidence": confidence,
                })
            if len(features) < PAGE_LIMIT:
                break
            offset += PAGE_LIMIT
    return slicks


async def sync_slicks(days: int | None = None) -> int:
    """Import slicks for all configured regions. Backfills BACKFILL_DAYS on an
    empty table, refreshes the last REFRESH_DAYS after that."""
    settings = get_settings()
    if not settings.ais_regions:
        return 0

    async with SessionLocal() as session:
        existing = await session.scalar(select(func.count(OilSlick.id)))
    if days is None:
        days = BACKFILL_DAYS if not existing else REFRESH_DAYS

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    total = 0
    for region in settings.ais_regions:
        try:
            slicks = await fetch_slicks(region.bbox, start, end)
        except Exception:
            logger.exception("Cerulean fetch failed for %s", region.name)
            continue
        if not slicks:
            continue
        async with SessionLocal() as session:
            for row in slicks:
                stmt = pg_insert(OilSlick).values(**row).on_conflict_do_nothing(index_elements=["id"])
                await session.execute(stmt)
            await session.commit()
        total += len(slicks)
        logger.info("Cerulean: %d slicks for %s (last %dd)", len(slicks), region.name, days)
    return total
