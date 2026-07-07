from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import AisGap, SarMatch, Vessel
from ..schemas import GapWithVesselOut, SarMatchOut

router = APIRouter(prefix="/api/gaps", tags=["gaps"])


@router.get("", response_model=list[GapWithVesselOut])
async def list_gaps(
    status: str | None = Query(None, pattern="^(open|closed)$"),
    limit: int = Query(100, le=500),
    session: AsyncSession = Depends(get_session),
):
    query = (
        select(AisGap, Vessel.name, Vessel.category)
        .outerjoin(Vessel, Vessel.mmsi == AisGap.mmsi)
        .order_by(AisGap.gap_start.desc())
        .limit(limit)
    )
    if status:
        query = query.where(AisGap.status == status)
    rows = (await session.execute(query)).all()

    gap_ids = [gap.id for gap, _, _ in rows]
    matches: dict[int, list[SarMatch]] = {}
    if gap_ids:
        for match in (await session.execute(
            select(SarMatch).where(SarMatch.gap_id.in_(gap_ids)).order_by(SarMatch.acquired_at)
        )).scalars():
            matches.setdefault(match.gap_id, []).append(match)

    return [
        GapWithVesselOut(
            **{c.name: getattr(gap, c.name) for c in AisGap.__table__.columns},
            vessel_name=name,
            category=category,
            sar_matches=[SarMatchOut.model_validate(m) for m in matches.get(gap.id, [])],
        )
        for gap, name, category in rows
    ]


@router.get("/{gap_id}/sar", response_model=list[SarMatchOut])
async def gap_sar_matches(gap_id: int, session: AsyncSession = Depends(get_session)):
    gap = await session.get(AisGap, gap_id)
    if not gap:
        raise HTTPException(404, "Unknown gap id")
    result = await session.execute(
        select(SarMatch).where(SarMatch.gap_id == gap_id).order_by(SarMatch.acquired_at)
    )
    return result.scalars().all()
