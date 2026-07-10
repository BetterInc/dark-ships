from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_superuser
from ..config import get_settings
from ..db import get_session
from ..models import Position, PositionCheck, Vessel, VesselRegistry
from ..schemas import PositionCheckOut, PositionOut, VesselCreate, VesselOut, VesselUpdate

router = APIRouter(prefix="/api/vessels", tags=["vessels"])


@router.get("/search")
async def search_vessels(q: str = Query(min_length=2), session: AsyncSession = Depends(get_session)):
    """Search by ship name, MMSI, or IMO. Returns matches with a live position
    (from the world snapshot) so the map can fly to them."""
    from ..ingest.aisstream import world_snapshot
    from .positions import ship_type_label

    digits = "".join(c for c in q if c.isdigit())
    conds = [VesselRegistry.name.ilike(f"%{q}%")]
    if len(digits) >= 4:
        conds.append(VesselRegistry.mmsi == int(digits) if len(digits) == 9 else False)
        conds.append(VesselRegistry.imo == digits)
    rows = (await session.execute(
        select(VesselRegistry).where(or_(*[c for c in conds if c is not False])).limit(25)
    )).scalars().all()

    mmsis = [r.mmsi for r in rows]
    watch = {v.mmsi: v for v in (await session.execute(
        select(Vessel).where(Vessel.mmsi.in_(mmsis), Vessel.active.is_(True)))).scalars()}
    # last stored position per ship - so watchlist ships not in the live
    # snapshot right now can still be located (fly to their last known spot)
    last_pos: dict[int, tuple[float, float]] = {}
    for mmsi, lat, lon in await session.execute(
        select(Position.mmsi, Position.lat, Position.lon)
        .where(Position.mmsi.in_(mmsis))
        .distinct(Position.mmsi).order_by(Position.mmsi, Position.ts.desc())
    ):
        last_pos[mmsi] = (lat, lon)

    out = []
    for r in rows:
        live = world_snapshot.get(r.mmsi)
        lat = live["lat"] if live else last_pos.get(r.mmsi, (None, None))[0]
        lon = live["lon"] if live else last_pos.get(r.mmsi, (None, None))[1]
        out.append({
            "mmsi": r.mmsi, "name": r.name, "imo": r.imo,
            "ship_type": ship_type_label(r.ship_type),
            "on_watchlist": r.mmsi in watch,
            "category": watch[r.mmsi].category if r.mmsi in watch else None,
            "lat": lat, "lon": lon, "live": live is not None,
            "last_seen": r.last_seen,
        })
    # locatable ships first, watchlist ahead of ambient
    out.sort(key=lambda x: (x["lat"] is None, not x["on_watchlist"]))
    return out


@router.get("/{mmsi}/info")
async def vessel_info(mmsi: int, session: AsyncSession = Depends(get_session)):
    """Everything we know about ANY vessel (not just the watchlist): registry
    identity + watchlist/risk status. Powers the click panel for ambient ships."""
    from .positions import ship_type_label

    reg = await session.get(VesselRegistry, mmsi)
    v = await session.scalar(select(Vessel).where(Vessel.mmsi == mmsi))
    return {
        "mmsi": mmsi,
        "name": (reg.name if reg else None) or (v.name if v else None),
        "imo": (reg.imo if reg else None) or (v.imo if v else None),
        "callsign": reg.callsign if reg else None,
        "ship_type": ship_type_label(reg.ship_type) if reg else None,
        "destination": reg.destination if reg else None,
        "flag": v.flag if v else None,
        "first_seen": reg.first_seen if reg else None,
        "last_seen": reg.last_seen if reg else None,
        "on_watchlist": bool(v and v.active),
        "category": v.category if (v and v.active) else None,
        "risk_score": v.risk_score if v else None,
        "notes": v.notes if v else None,
    }


@router.get("", response_model=list[VesselOut])
async def list_vessels(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Vessel).order_by(Vessel.created_at.desc()))
    return result.scalars().all()


@router.post("", response_model=VesselOut, status_code=201,
             dependencies=[Depends(current_superuser)])
async def create_vessel(payload: VesselCreate, session: AsyncSession = Depends(get_session)):
    exists = await session.scalar(select(Vessel).where(Vessel.mmsi == payload.mmsi))
    if exists:
        raise HTTPException(409, f"MMSI {payload.mmsi} is already on the watchlist")
    vessel = Vessel(**payload.model_dump())
    session.add(vessel)
    await session.commit()
    await session.refresh(vessel)
    return vessel


@router.patch("/{mmsi}", response_model=VesselOut,
              dependencies=[Depends(current_superuser)])
async def update_vessel(mmsi: int, payload: VesselUpdate, session: AsyncSession = Depends(get_session)):
    vessel = await session.scalar(select(Vessel).where(Vessel.mmsi == mmsi))
    if not vessel:
        raise HTTPException(404, "Unknown MMSI")
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("followed"):
        followed = await session.scalar(
            select(func.count()).select_from(Vessel)
            .where(Vessel.followed.is_(True), Vessel.active.is_(True), Vessel.mmsi != mmsi)
        )
        if followed >= get_settings().follow_max:
            raise HTTPException(409, f"Follow list is full ({get_settings().follow_max} - "
                                     "the AISstream MMSI-filter limit). Unfollow a ship first.")
        # a manual follow is user intent: pin it so the engine never rotates it out
        vessel.pinned = True
    for field, value in changes.items():
        setattr(vessel, field, value)
    await session.commit()
    await session.refresh(vessel)
    return vessel


@router.delete("/{mmsi}", status_code=204,
               dependencies=[Depends(current_superuser)])
async def delete_vessel(mmsi: int, session: AsyncSession = Depends(get_session)):
    vessel = await session.scalar(select(Vessel).where(Vessel.mmsi == mmsi))
    if not vessel:
        raise HTTPException(404, "Unknown MMSI")
    if vessel.auto_added:
        # suppress instead of delete: risk events persist for 30 days, so a
        # deleted row would just get resurrected on the next scan
        vessel.active = False
        vessel.followed = False
        vessel.pinned = True  # user override - the engine stays out
    else:
        await session.delete(vessel)
    await session.commit()


@router.get("/{mmsi}/position-checks", response_model=list[PositionCheckOut])
async def position_checks(mmsi: int, session: AsyncSession = Depends(get_session)):
    """Satellite scenes that captured this vessel's claimed AIS positions -
    if the ship says it's there, the image should show a hull there."""
    result = await session.execute(
        select(PositionCheck)
        .where(PositionCheck.mmsi == mmsi)
        .order_by(PositionCheck.acquired_at.desc())
        .limit(20)
    )
    return result.scalars().all()


@router.get("/{mmsi}/track", response_model=list[PositionOut])
async def vessel_track(
    mmsi: int,
    hours: float = Query(48, le=24 * 30),
    session: AsyncSession = Depends(get_session),
):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    result = await session.execute(
        select(Position)
        .where(Position.mmsi == mmsi, Position.ts >= since)
        .order_by(Position.ts.asc())
        .limit(5000)
    )
    return result.scalars().all()
