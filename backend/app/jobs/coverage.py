"""Feed-coverage guard.

A ship can only be said to have "gone dark" if we were listening the whole
time. covered_since() returns the earliest moment T such that the ingest
heartbeat runs unbroken (no hole > max_hole_minutes) from T to now - gap
rules must ignore any silence that started before T, or every `docker
compose down` would flood the system with false gaps on restart.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from ..models import IngestHeartbeat

MAX_HOLE_MINUTES = 15
LOOKBACK_HOURS = 72


async def covered_since(session) -> datetime:
    now = datetime.now(timezone.utc)
    rows = (await session.execute(
        select(IngestHeartbeat.ts)
        .where(IngestHeartbeat.ts >= now - timedelta(hours=LOOKBACK_HOURS))
        .order_by(IngestHeartbeat.ts.desc())
    )).scalars().all()
    if not rows:
        return now  # never listened → nothing is provably a gap
    max_hole = timedelta(minutes=MAX_HOLE_MINUTES)
    edge = now
    for ts in rows:
        if edge - ts > max_hole:
            break
        edge = ts
    return edge


async def prune_heartbeats(session) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    await session.execute(delete(IngestHeartbeat).where(IngestHeartbeat.ts < cutoff))
