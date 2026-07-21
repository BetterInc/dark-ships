"""Digitraffic (Finnish Transport Agency) - a free national AIS source feeding
the one unified position stream.

Every AIS provider writes to the SAME latest_positions / positions tables; the
rest of the system reads that one stream and never distinguishes who delivered a
fix. `source` is just provenance metadata.

Digitraffic is genuinely free and unlimited, so it runs CONTINUOUSLY - a
permanent always-on contributor for the Gulf of Finland
/ northern Baltic. That happens to be the main Russian shadow-fleet oil export
corridor (Primorsk, Ust-Luga, St. Petersburg, Sköldvik), so this keeps the most
important region live even while the aisstream world firehose is healthy, and
covers it fully when aisstream is down.

The GeoJSON locations endpoint needs a Digitraffic-User header and gzip
(it 406s otherwise). Positions are only written when they ADVANCE a ship's
last-known timestamp, which dedups both re-polled stationary ships and any
overlap with aisstream's Baltic coverage - so the history table stays clean.
"""

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..config import get_settings
from ..db import SessionLocal
from ..models import IngestHeartbeat, LatestPosition, Position

logger = logging.getLogger(__name__)

LOCATIONS_URL = "https://meri.digitraffic.fi/api/ais/v1/locations"
# Digitraffic asks every client to identify itself; gzip is mandatory.
HEADERS = {"Digitraffic-User": "DarkShips/ais-failover", "Accept-Encoding": "gzip"}
TIMEOUT = 25.0
MAX_AGE = timedelta(hours=6)   # ignore fixes older than this (stale on arrival)


async def run_digitraffic() -> None:
    """Pull the Finnish AIS picture and contribute fresh fixes to the stream."""
    s = get_settings()
    if not getattr(s, "digitraffic_enabled", True):
        return

    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(LOCATIONS_URL, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            payload = r.json()
    except Exception as e:
        logger.warning("Digitraffic fetch failed: %s", e)
        return

    now = datetime.now(timezone.utc)
    floor = now - MAX_AGE
    # newest fix per mmsi from the feed (GeoJSON FeatureCollection)
    newest_by: dict[int, dict] = {}
    for feat in payload.get("features", []):
        p = feat.get("properties") or {}
        coords = (feat.get("geometry") or {}).get("coordinates") or []
        mmsi = feat.get("mmsi") or p.get("mmsi")
        ext = p.get("timestampExternal")
        if not mmsi or len(coords) < 2 or ext is None:
            continue
        try:
            ts = datetime.fromtimestamp(ext / 1000, tz=timezone.utc)
        except (ValueError, OSError, TypeError):
            continue
        if ts < floor or ts > now + timedelta(minutes=5):
            continue  # stale, or a clock-skew fix in the future
        lon, lat = float(coords[0]), float(coords[1])
        row = {
            "mmsi": int(mmsi), "ts": ts,
            "lat": round(lat, 5), "lon": round(lon, 5),
            "sog": p.get("sog"), "cog": p.get("cog"),
            "heading": p.get("heading") if p.get("heading") not in (511, None) else None,
            "nav_status": p.get("navStat"),
            "ship_name": None,  # names come from the registry, not this feed
            "source": "digitraffic",
        }
        cur = newest_by.get(row["mmsi"])
        if cur is None or row["ts"] > cur["ts"]:
            newest_by[row["mmsi"]] = row

    if not newest_by:
        return

    async with SessionLocal() as session:
        # Only write positions that ADVANCE a ship's last-known ts. This dedups
        # re-polled stationary ships and any overlap with aisstream's Baltic
        # coverage against ONE source of truth (latest_positions), so the
        # history table never accumulates cross-source duplicate rows.
        mmsis = list(newest_by.keys())
        existing = dict((await session.execute(
            select(LatestPosition.mmsi, LatestPosition.ts)
            .where(LatestPosition.mmsi.in_(mmsis)))).all())
        fresh = [row for m, row in newest_by.items()
                 if m not in existing or row["ts"] > existing[m]]
        if not fresh:
            return

        lp = pg_insert(LatestPosition).values(fresh)
        await session.execute(lp.on_conflict_do_update(
            index_elements=["mmsi"],
            set_={c: getattr(lp.excluded, c) for c in
                  ("ts", "lat", "lon", "sog", "cog", "heading",
                   "nav_status", "ship_name", "source")},
            where=lp.excluded.ts > LatestPosition.ts))
        await session.execute(pg_insert(Position).values(fresh))
        minute = now.replace(second=0, microsecond=0)
        await session.execute(pg_insert(IngestHeartbeat)
                              .values(ts=minute, n=len(fresh))
                              .on_conflict_do_update(
                                  index_elements=["ts"],
                                  set_={"n": IngestHeartbeat.n + len(fresh)}))
        await session.commit()
        logger.info("Digitraffic: wrote %d fresh positions (%d in feed)",
                    len(fresh), len(newest_by))
