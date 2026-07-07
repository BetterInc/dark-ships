from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
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


@router.get("", response_model=list[ImageryItem])
async def list_imagery(session: AsyncSession = Depends(get_session)):
    """All satellite evidence in one place: scenes that captured claimed
    positions of watchlist ships, and scenes intersecting AIS-gap drift areas."""
    items: list[ImageryItem] = []

    names = {v.mmsi: v.name for v in (await session.execute(select(Vessel))).scalars()}

    for c in (await session.execute(
        select(PositionCheck).order_by(PositionCheck.acquired_at.desc()).limit(100)
    )).scalars():
        items.append(ImageryItem(
            kind="position_check", mmsi=c.mmsi, vessel_name=names.get(c.mmsi),
            source=c.source, acquired_at=c.acquired_at,
            lat=c.claimed_lat, lon=c.claimed_lon, delta_minutes=c.delta_minutes,
            product_name=c.product_name, quicklook_url=c.quicklook_url,
            browser_url=c.browser_url,
        ))

    gaps = {g.id: g for g in (await session.execute(select(AisGap))).scalars()}
    for m in (await session.execute(
        select(SarMatch).order_by(SarMatch.acquired_at.desc()).limit(100)
    )).scalars():
        gap = gaps.get(m.gap_id)
        if gap is None:
            continue
        items.append(ImageryItem(
            kind="gap_scene", mmsi=gap.mmsi, vessel_name=names.get(gap.mmsi),
            source=m.source, acquired_at=m.acquired_at,
            lat=gap.last_lat, lon=gap.last_lon, gap_id=m.gap_id,
            product_name=m.product_name, quicklook_url=m.quicklook_url,
            browser_url=m.browser_url,
        ))

    items.sort(key=lambda i: i.acquired_at, reverse=True)
    return items[:100]
