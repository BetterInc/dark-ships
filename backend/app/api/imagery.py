from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import AisGap, PositionCheck, SarMatch, Vessel

router = APIRouter(prefix="/api/imagery", tags=["imagery"])


class ImageryItem(BaseModel):
    kind: str  # position_check | gap_scene
    mmsi: int
    vessel_name: str | None
    source: str
    acquired_at: datetime
    lat: float
    lon: float
    delta_minutes: float | None = None  # position checks: |claim - capture|
    gap_id: int | None = None
    product_name: str | None
    quicklook_url: str | None
    browser_url: str | None
    # automated SAR detection (position checks only)
    check_id: int | None = None
    hull_detected: bool | None = None
    target_count: int | None = None
    nearest_offset_m: float | None = None
    persistent_target: bool | None = None
    target_length_m: float | None = None
    size_match: bool | None = None
    chip_key: str | None = None  # set = chip at /api/position-checks/{check_id}/chip


class ImageryPage(BaseModel):
    total: int
    items: list[ImageryItem]


@router.get("", response_model=ImageryPage)
async def list_imagery(
    page: int = Query(0, ge=0),
    page_size: int = Query(25, ge=1, le=100),
    kind: str | None = Query(None, pattern="^(position_check|gap_scene)$"),
    source: str | None = Query(None, pattern="^(sentinel-1|sentinel-2)$"),
    verdict: str | None = Query(None, pattern="^(hull|no_target|pending)$"),
    session: AsyncSession = Depends(get_session),
):
    """All satellite evidence in one place, newest first, server-paginated:
    scenes that captured claimed positions of watchlist ships (with the
    automated SAR verdict + stored chip when analyzed) and scenes intersecting
    AIS-gap drift areas. The two sources are merged by acquisition time; both
    are fetched up to the page end and merge-sorted, so a page is exact while
    deep pages stay bounded."""
    want_checks = kind in (None, "position_check")
    # gap scenes carry no detection verdict, so a verdict filter excludes them
    want_gaps = kind in (None, "gap_scene") and verdict is None
    fetch = (page + 1) * page_size

    total = 0
    items: list[ImageryItem] = []

    if want_checks:
        q = select(PositionCheck)
        if source:
            q = q.where(PositionCheck.source == source)
        if verdict == "hull":
            q = q.where(PositionCheck.hull_detected.is_(True))
        elif verdict == "no_target":
            q = q.where(PositionCheck.hull_detected.is_(False))
        elif verdict == "pending":
            q = q.where(PositionCheck.hull_detected.is_(None))
        total += await session.scalar(
            select(func.count()).select_from(q.subquery())) or 0
        for c in (await session.execute(
            q.order_by(PositionCheck.acquired_at.desc()).limit(fetch)
        )).scalars():
            items.append(ImageryItem(
                kind="position_check", mmsi=c.mmsi, vessel_name=None,
                source=c.source, acquired_at=c.acquired_at,
                lat=c.claimed_lat, lon=c.claimed_lon,
                delta_minutes=c.delta_minutes, product_name=c.product_name,
                quicklook_url=c.quicklook_url, browser_url=c.browser_url,
                check_id=c.id, hull_detected=c.hull_detected,
                target_count=c.target_count,
                nearest_offset_m=c.nearest_offset_m,
                persistent_target=c.persistent_target,
                target_length_m=c.target_length_m, size_match=c.size_match,
                chip_key=c.chip_key,
            ))

    if want_gaps:
        q = (select(SarMatch, AisGap)
             .join(AisGap, AisGap.id == SarMatch.gap_id))
        if source:
            q = q.where(SarMatch.source == source)
        total += await session.scalar(
            select(func.count()).select_from(q.subquery())) or 0
        for m, gap in (await session.execute(
            q.order_by(SarMatch.acquired_at.desc()).limit(fetch)
        )).all():
            items.append(ImageryItem(
                kind="gap_scene", mmsi=gap.mmsi, vessel_name=None,
                source=m.source, acquired_at=m.acquired_at,
                lat=gap.last_lat, lon=gap.last_lon, gap_id=m.gap_id,
                product_name=m.product_name, quicklook_url=m.quicklook_url,
                browser_url=m.browser_url,
            ))

    items.sort(key=lambda i: i.acquired_at, reverse=True)
    items = items[page * page_size: (page + 1) * page_size]

    mmsis = {i.mmsi for i in items}
    if mmsis:
        names = {v.mmsi: v.name for v in (await session.execute(
            select(Vessel).where(Vessel.mmsi.in_(mmsis)))).scalars()}
        for i in items:
            i.vessel_name = names.get(i.mmsi)

    return ImageryPage(total=total, items=items)
