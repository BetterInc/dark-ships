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


# ---- retroactive outage sweep --------------------------------------------

SWEEP_LOOKBACK_DAYS = 30     # match the scoring window - older events age out
SWEEP_FLOOR_RATIO = 0.4      # hour below this fraction of median volume = deaf
SWEEP_RULES = ("regional_gap", "drift_mismatch", "midsea_appearance")


async def sweep_outage_gap_events() -> None:
    """Daily self-heal: delete gap-family risk events whose evidence window
    overlaps an hour in which WE were deaf (hourly global position volume
    far below the recent median - full DB, degraded feed, downtime). The
    silence of one ship proves nothing when every ship went silent; scores
    recompute on the next behaviour scan, so wrongly-greened ships demote
    on their own."""
    import json

    from sqlalchemy import text

    from ..db import SessionLocal

    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        counts = {h: n for h, n in await session.execute(text(
            "SELECT date_trunc('hour', ts) AS h, count(*) AS n FROM positions "
            "WHERE ts > now() - interval '30 days' GROUP BY 1"))}
        if len(counts) < 24:
            return  # not enough history to define "normal"
        med = median(counts.values())
        floor = SWEEP_FLOOR_RATIO * med
        start, end = min(counts), max(counts)
        deaf = set()
        h = start
        while h <= end:
            if counts.get(h, 0) < floor:
                deaf.add(h)
            h += timedelta(hours=1)
        if not deaf:
            return

        def window_overlaps_deaf(begin: datetime, until: datetime) -> bool:
            h = begin.replace(minute=0, second=0, microsecond=0)
            while h <= until:
                if h in deaf:
                    return True
                h += timedelta(hours=1)
            return False

        evts = (await session.execute(text(
            "SELECT id, rule, ts, details FROM risk_events "
            "WHERE rule = ANY(:rules) AND ts > :cutoff",
        ).bindparams(rules=list(SWEEP_RULES),
                     cutoff=now - timedelta(days=SWEEP_LOOKBACK_DAYS)))).all()
        doomed = []
        for eid, rule, ts, details in evts:
            try:
                d = json.loads(details) if details else {}
            except ValueError:
                d = {}
            if rule == "regional_gap":
                raw = d.get("last_ts")
                begin = (datetime.fromisoformat(raw) if raw
                         else ts - timedelta(hours=12))
            elif rule == "drift_mismatch":
                begin = ts - timedelta(hours=float(d.get("hours", 12)))
            else:  # midsea_appearance: appearing right after deafness = recovery
                begin = ts - timedelta(hours=2)
            if begin.tzinfo is None:
                begin = begin.replace(tzinfo=timezone.utc)
            if window_overlaps_deaf(begin, ts):
                doomed.append(eid)
        for i in range(0, len(doomed), 5000):
            await session.execute(
                text("DELETE FROM risk_events WHERE id = ANY(:ids)"),
                {"ids": doomed[i:i + 5000]})
        await session.commit()
        if doomed:
            import logging
            logging.getLogger(__name__).warning(
                "Coverage sweep: deleted %d gap-family events overlapping %d "
                "deaf hours (receiver outage, not ship behaviour)",
                len(doomed), len(deaf))
