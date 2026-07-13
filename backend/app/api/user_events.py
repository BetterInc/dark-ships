"""Per-user risk-event feed.

A live-ish chronological feed of every risk event fired for the vessels on the
current user's private watchlist (the UserVessel table). Newest first, in plain
language. Requires an authenticated user (current_active_user).
"""

import json
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_active_user
from ..db import get_session
from ..models import RiskEvent, User, UserVessel, VesselRegistry
from .positions import PATTERN_LABELS

router = APIRouter(prefix="/api/me", tags=["events"])

# Rules that represent an actual government / enforcement sanction listing (as
# opposed to a port-state detention or a tanker-tracker watch entry). A hit on
# one of these is always high severity regardless of its numeric score.
SANCTION_RULES = {
    "risklist_ofac",
    "risklist_eu",
    "risklist_uk",
    "risklist_un1718",
    "risklist_canada",
    "risklist_switzerland",
    "risklist_australia",
    "risklist_iuu",
    "risklist_eu_iuu",
    "risklist_nz",
    "risklist_iccat",
    "risklist_wcpfc",
}


class EventOut(BaseModel):
    mmsi: int
    vessel_name: str
    rule: str
    label: str
    severity: str  # high | medium | low
    score: float
    ts: datetime
    detail: dict


def _severity(rule: str, score: float) -> str:
    if score >= 80 or rule in SANCTION_RULES:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


@router.get("/events", response_model=list[EventOut])
async def my_events(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
):
    mmsis = list((await session.execute(
        select(UserVessel.mmsi).where(UserVessel.user_id == user.id)
    )).scalars())
    if not mmsis:
        return []

    events = list((await session.execute(
        select(RiskEvent)
        .where(RiskEvent.mmsi.in_(mmsis))
        .order_by(RiskEvent.ts.desc())
        .limit(200)
    )).scalars())

    # vessel identity (name) for every mmsi we are about to return
    names = {
        r.mmsi: r.name for r in (await session.execute(
            select(VesselRegistry).where(VesselRegistry.mmsi.in_(mmsis)))).scalars()
    }

    out: list[EventOut] = []
    for e in events:
        detail: dict = {}
        if e.details:
            try:
                parsed = json.loads(e.details)
                if isinstance(parsed, dict):
                    detail = parsed
            except (ValueError, TypeError):
                detail = {}
        out.append(EventOut(
            mmsi=e.mmsi,
            vessel_name=names.get(e.mmsi) or str(e.mmsi),
            rule=e.rule,
            label=PATTERN_LABELS.get(e.rule, e.rule),
            severity=_severity(e.rule, e.score),
            score=e.score,
            ts=e.ts,
            detail=detail,
        ))
    return out
