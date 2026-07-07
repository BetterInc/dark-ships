"""Per-user private watchlist.

Each user keeps their OWN list of followed ships. Following is served entirely
from our recorded position history (we record every ship at 1/min), so it does
not consume any provider slot. Free tier is capped at `free_follow_limit`.

All endpoints require an authenticated user (current_active_user).
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_active_user
from ..config import get_settings
from ..db import get_session
from ..jobs.behavior import SCORE_WINDOW_DAYS
from ..models import Position, RiskEvent, User, UserVessel, Vessel, VesselRegistry
from .positions import ship_type_label

router = APIRouter(prefix="/api/me", tags=["watchlist"])


# ---- response shapes -----------------------------------------------------

class WatchPosition(BaseModel):
    lat: float
    lon: float
    sog: float | None = None
    cog: float | None = None
    ts: datetime
    live: bool  # true when taken from the in-memory world snapshot


class WatchRisk(BaseModel):
    category: str | None = None
    risk_score: float | None = None
    on_suggestions: bool = False  # currently visible on the public Suggestions feed


class WatchlistItemOut(BaseModel):
    mmsi: int
    name: str | None = None
    ship_type: str | None = None
    note: str | None = None
    created_at: datetime
    position: WatchPosition | None = None
    risk: WatchRisk | None = None  # present only when the ship is system-known


class WatchlistAdd(BaseModel):
    mmsi: int = Field(ge=100_000_000, le=999_999_999)
    note: str | None = None


class WatchlistNote(BaseModel):
    note: str | None = None


class WatchlistEntryOut(BaseModel):
    """Bare record returned by add/patch - the list endpoint enriches it."""
    mmsi: int
    note: str | None = None
    created_at: datetime


# ---- helpers -------------------------------------------------------------

async def _enrich(session: AsyncSession, entries: list[UserVessel]) -> list[WatchlistItemOut]:
    from ..ingest.aisstream import world_snapshot

    mmsis = [e.mmsi for e in entries]
    if not mmsis:
        return []

    # registry identity (name + ship type)
    registry = {
        r.mmsi: r for r in (await session.execute(
            select(VesselRegistry).where(VesselRegistry.mmsi.in_(mmsis)))).scalars()
    }
    # newest recorded position per ship
    latest = (
        select(Position)
        .where(Position.mmsi.in_(mmsis))
        .distinct(Position.mmsi)
        .order_by(Position.mmsi, Position.ts.desc())
        .subquery()
    )
    db_pos = {row.mmsi: row for row in (await session.execute(select(latest))).all()}
    # system risk context (category / score) for ships also in the vessels table
    vessels = {
        v.mmsi: v for v in (await session.execute(
            select(Vessel).where(Vessel.mmsi.in_(mmsis), Vessel.active.is_(True)))).scalars()
    }
    # ships currently on the public Suggestions feed (same bar as /api/suggestions)
    since = datetime.now(timezone.utc) - timedelta(days=SCORE_WINDOW_DAYS)
    threshold = get_settings().suggestion_min_score / 2
    suggested = {
        row[0] for row in await session.execute(
            select(RiskEvent.mmsi)
            .where(RiskEvent.mmsi.in_(mmsis), RiskEvent.ts >= since)
            .group_by(RiskEvent.mmsi)
            .having(func.sum(RiskEvent.score) >= threshold)
        )
    }

    items: list[WatchlistItemOut] = []
    for e in entries:
        reg = registry.get(e.mmsi)
        live = world_snapshot.get(e.mmsi)
        dbp = db_pos.get(e.mmsi)

        position: WatchPosition | None = None
        # prefer whichever position is fresher: the live snapshot or the DB row
        live_ts = live["ts"] if live else None
        db_ts = dbp.ts if dbp else None
        if live_ts and (db_ts is None or live_ts >= db_ts):
            position = WatchPosition(lat=live["lat"], lon=live["lon"], sog=live.get("sog"),
                                     cog=live.get("cog"), ts=live_ts, live=True)
        elif dbp is not None:
            position = WatchPosition(lat=dbp.lat, lon=dbp.lon, sog=dbp.sog,
                                     cog=dbp.cog, ts=dbp.ts, live=False)

        risk: WatchRisk | None = None
        v = vessels.get(e.mmsi)
        if v is not None:
            risk = WatchRisk(category=v.category, risk_score=v.risk_score,
                             on_suggestions=e.mmsi in suggested)

        items.append(WatchlistItemOut(
            mmsi=e.mmsi,
            name=(reg.name if reg else None),
            ship_type=ship_type_label(reg.ship_type) if reg else None,
            note=e.note,
            created_at=e.created_at,
            position=position,
            risk=risk,
        ))

    # live / most-recently-seen ships first; ships with no known position last
    items.sort(key=lambda i: (i.position is None, -(i.position.ts.timestamp() if i.position else 0)))
    return items


async def _user_entries(session: AsyncSession, user_id: int) -> list[UserVessel]:
    return list((await session.execute(
        select(UserVessel).where(UserVessel.user_id == user_id)
    )).scalars())


# ---- endpoints -----------------------------------------------------------

@router.get("/watchlist", response_model=list[WatchlistItemOut])
async def my_watchlist(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
):
    entries = await _user_entries(session, user.id)
    return await _enrich(session, entries)


@router.post("/watchlist", response_model=WatchlistEntryOut, status_code=201)
async def add_to_watchlist(
    payload: WatchlistAdd,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
):
    existing = await session.scalar(
        select(UserVessel).where(UserVessel.user_id == user.id, UserVessel.mmsi == payload.mmsi)
    )
    if existing is not None:
        raise HTTPException(409, f"MMSI {payload.mmsi} is already on your watchlist")

    limit = get_settings().free_follow_limit
    count = await session.scalar(
        select(func.count()).select_from(UserVessel).where(UserVessel.user_id == user.id)
    )
    if count >= limit:
        raise HTTPException(
            409, f"Free plan is limited to {limit} followed ships. Remove one first.")

    entry = UserVessel(user_id=user.id, mmsi=payload.mmsi, note=payload.note)
    session.add(entry)
    try:
        await session.commit()
    except IntegrityError:
        # lost a race on the unique (user_id, mmsi) constraint
        await session.rollback()
        raise HTTPException(409, f"MMSI {payload.mmsi} is already on your watchlist")
    await session.refresh(entry)
    return WatchlistEntryOut(mmsi=entry.mmsi, note=entry.note, created_at=entry.created_at)


@router.patch("/watchlist/{mmsi}", response_model=WatchlistEntryOut)
async def update_note(
    mmsi: int,
    payload: WatchlistNote,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
):
    entry = await session.scalar(
        select(UserVessel).where(UserVessel.user_id == user.id, UserVessel.mmsi == mmsi)
    )
    if entry is None:
        raise HTTPException(404, f"MMSI {mmsi} is not on your watchlist")
    entry.note = payload.note
    await session.commit()
    await session.refresh(entry)
    return WatchlistEntryOut(mmsi=entry.mmsi, note=entry.note, created_at=entry.created_at)


@router.delete("/watchlist/{mmsi}", status_code=204)
async def remove_from_watchlist(
    mmsi: int,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        delete(UserVessel).where(UserVessel.user_id == user.id, UserVessel.mmsi == mmsi)
    )
    if result.rowcount == 0:
        raise HTTPException(404, f"MMSI {mmsi} is not on your watchlist")
    await session.commit()
