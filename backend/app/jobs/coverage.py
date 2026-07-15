"""Feed-coverage guard.

A ship can only be said to have "gone dark" if we were listening the whole
time. covered_since() returns the earliest moment T such that the ingest ran
unbroken AND healthy from T to now - gap rules must ignore any silence that
started before T, or every `docker compose down` would flood the system with
false gaps on restart.

Two failure modes are covered:
- unbroken: no heartbeat hole > MAX_HOLE_MINUTES (process down);
- healthy:  per-bucket flushed-position volume not collapsed against the
  recent median. A full database or a degraded upstream feed leaves the
  process alive (heartbeats keep landing) while the receiver is effectively
  deaf - July 2026 audit: one such day minted ~10k false "went dark" events.
"""

from datetime import datetime, timedelta, timezone
from statistics import median

from sqlalchemy import delete, select

from ..models import IngestHeartbeat

MAX_HOLE_MINUTES = 15
LOOKBACK_HOURS = 72
VOLUME_BUCKET_MIN = 10       # sum heartbeat volumes per 10-minute bucket
VOLUME_FLOOR_RATIO = 0.35    # bucket below this fraction of the median = deaf
VOLUME_MIN_BUCKETS = 12      # need 2h of volume data before judging health


def _bucket(ts: datetime) -> datetime:
    return ts.replace(minute=ts.minute - ts.minute % VOLUME_BUCKET_MIN,
                      second=0, microsecond=0)


async def covered_since(session) -> datetime:
    now = datetime.now(timezone.utc)
    rows = (await session.execute(
        select(IngestHeartbeat.ts, IngestHeartbeat.n)
        .where(IngestHeartbeat.ts >= now - timedelta(hours=LOOKBACK_HOURS))
        .order_by(IngestHeartbeat.ts.desc())
    )).all()
    if not rows:
        return now  # never listened → nothing is provably a gap

    # volume per bucket (only rows that carry a count; pre-upgrade rows are
    # NULL and judged by presence alone, as before)
    buckets: dict[datetime, int] = {}
    for ts, n in rows:
        if n is not None:
            b = _bucket(ts)
            buckets[b] = buckets.get(b, 0) + n
    current = _bucket(now)  # still filling - never judge it
    complete = {b: v for b, v in buckets.items() if b != current}
    floor = (VOLUME_FLOOR_RATIO * median(complete.values())
             if len(complete) >= VOLUME_MIN_BUCKETS else 0)

    max_hole = timedelta(minutes=MAX_HOLE_MINUTES)
    edge = now
    for ts, n in rows:
        if edge - ts > max_hole:
            break  # process was down
        if floor and n is not None:
            b = _bucket(ts)
            if b != current and buckets.get(b, 0) < floor:
                break  # process alive but the receiver was effectively deaf
        edge = ts
    return edge


async def prune_heartbeats(session) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    await session.execute(delete(IngestHeartbeat).where(IngestHeartbeat.ts < cutoff))
