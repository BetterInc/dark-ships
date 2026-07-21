"""VesselAPI - a secondary provider feeding the one unified position stream.

Every AIS provider writes to the SAME latest_positions / positions tables; the
rest of the system (map, clusters, behaviour engine, SAR, digests) reads that
one stream and never distinguishes who delivered a fix. `source` is just
provenance metadata, not a separate data path.

aisstream.io carries the firehose; VesselAPI (vesselapi.com) is a free-tier
REST bounding-box source - not a full replacement, but enough to keep the
MONITORED REGIONS live when aisstream stalls. The only source-aware logic lives
HERE, in provider selection: to respect the free tier we spend VesselAPI quota
only while the *primary* feed is stale (checked below), then contribute to the
same stream tagged source='vesselapi' plus an ingest heartbeat.
"""

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..config import Region, get_settings
from ..db import SessionLocal
from ..models import IngestHeartbeat, LatestPosition, Position

logger = logging.getLogger(__name__)

BASE = "https://api.vesselapi.com/v1/location/vessels/bounding-box"
STALE_AFTER = timedelta(hours=1)   # primary considered down past this
TILE_DEG = 2.0                     # VesselAPI rejects boxes larger than ~2 degrees
MAX_TILES = 80                     # request budget per run (only spent during outages)
MAX_PAGES_PER_TILE = 4             # dense coastal tiles paginate; open sea is 1 page
PAGE_TIMEOUT = 20.0

# aisstream carries a world firehose that needs no region list, so prod often
# runs with ais_regions empty. VesselAPI can only query bounding boxes, so it
# needs its own coverage: the known shadow-fleet / sanctions / narco corridors
# where keeping ships visible during an outage matters most. Used whenever no
# regions are configured, so the failover is self-sufficient. bbox is
# [[latMin, lonMin], [latMax, lonMax]]; tiled to <=2deg and capped by MAX_TILES.
DEFAULT_FAILOVER_REGIONS = [
    Region(name="Fujairah / Gulf of Oman", bbox=[[24.0, 56.0], [26.5, 58.5]]),
    Region(name="Strait of Hormuz", bbox=[[25.5, 54.5], [27.0, 57.0]]),
    Region(name="Laconian Gulf", bbox=[[35.5, 22.0], [37.5, 24.5]]),
    Region(name="Kerch Strait / NE Black Sea", bbox=[[44.0, 35.5], [46.0, 38.5]]),
    Region(name="Gulf of Finland", bbox=[[59.0, 22.0], [60.7, 28.5]]),
    Region(name="Malacca / Singapore Strait", bbox=[[0.5, 100.0], [4.5, 104.5]]),
    Region(name="Gibraltar / Ceuta", bbox=[[35.5, -6.0], [36.3, -4.5]]),
    Region(name="SE Caribbean / Venezuela", bbox=[[9.5, -68.0], [12.5, -61.0]]),
    Region(name="Gulf of Guinea / Lome", bbox=[[3.5, 0.0], [6.5, 4.0]]),
]


def _tiles(bbox):
    """Split a region bbox into <=TILE_DEG boxes VesselAPI will accept."""
    (lat0, lon0), (lat1, lon1) = bbox
    la = lat0
    while la < lat1:
        lo = lon0
        while lo < lon1:
            yield (min(la, lat1 - 0.001), min(lo, lon1 - 0.001),
                   min(la + TILE_DEG, lat1), min(lo + TILE_DEG, lon1))
            lo += TILE_DEG
        la += TILE_DEG


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


async def _fetch_tile(client: httpx.AsyncClient, key: str, tile) -> tuple[list[dict], bool]:
    """All vessels in one <=2deg tile, paginated. Returns (rows, rate_limited)."""
    lat0, lon0, lat1, lon1 = tile
    rows: list[dict] = []
    token = None
    for _ in range(MAX_PAGES_PER_TILE):
        params = {
            "filter.latBottom": lat0, "filter.latTop": lat1,
            "filter.lonLeft": lon0, "filter.lonRight": lon1,
        }
        if token:
            params["nextToken"] = token
        try:
            r = await client.get(BASE, params=params,
                                  headers={"Authorization": f"Bearer {key}"},
                                  timeout=PAGE_TIMEOUT)
            if r.status_code == 429:
                return rows, True  # free-tier budget exhausted - stop the run
            r.raise_for_status()
            payload = r.json()
        except Exception:
            break
        for v in payload.get("vessels", []):
            if v.get("suspected_glitch"):
                continue  # provider already flags likely-bad fixes
            ts = _parse_ts(v.get("timestamp"))
            lat, lon = v.get("latitude"), v.get("longitude")
            mmsi = v.get("mmsi")
            if ts is None or lat is None or lon is None or not mmsi:
                continue
            rows.append({
                "mmsi": int(mmsi), "ts": ts,
                "lat": round(float(lat), 5), "lon": round(float(lon), 5),
                "sog": v.get("sog"), "cog": v.get("cog"),
                "heading": v.get("heading"),
                "nav_status": v.get("nav_status"),
                "ship_name": (v.get("vessel_name") or "").strip() or None,
                "source": "vesselapi",
            })
        token = payload.get("nextToken")
        if not token:
            break
    return rows, False


async def run_vesselapi_failover() -> None:
    """Poll VesselAPI for the monitored regions and contribute to the stream -
    but ONLY while the primary feed is stale, so the free tier isn't spent while
    aisstream is healthy."""
    s = get_settings()
    if not s.vesselapi_key:
        return
    async with SessionLocal() as session:
        # Gate on the PRIMARY provider's freshness, not the whole stream: our
        # own writes (source='vesselapi') would otherwise keep max(ts) fresh and
        # suppress every subsequent poll after the first one.
        newest = await session.scalar(select(func.max(LatestPosition.ts)).where(
            LatestPosition.source != "vesselapi"))
        now = datetime.now(timezone.utc)
        if newest is not None and (now - newest) < STALE_AFTER:
            return  # primary feed is fresh - don't spend quota

        # configured regions if any, else the built-in shadow-fleet corridors -
        # so the failover works even when prod runs world-feed-only (no regions).
        regions = s.ais_regions or DEFAULT_FAILOVER_REGIONS
        # tile all regions into <=2deg boxes, priority order (config lists the
        # shadow-fleet regions first), capped to a per-run request budget
        tiles = [t for reg in regions for t in _tiles(reg.bbox)][:MAX_TILES]
        rows: list[dict] = []
        async with httpx.AsyncClient() as client:
            for tile in tiles:
                tile_rows, limited = await _fetch_tile(client, s.vesselapi_key, tile)
                rows += tile_rows
                if limited:
                    logger.info("VesselAPI failover: rate-limited after partial coverage")
                    break

        # newest fix per mmsi
        newest_by: dict[int, dict] = {}
        for r in rows:
            cur = newest_by.get(r["mmsi"])
            if cur is None or r["ts"] > cur["ts"]:
                newest_by[r["mmsi"]] = r
        if not newest_by:
            logger.warning("VesselAPI failover: no positions returned")
            return

        vals = list(newest_by.values())
        # latest_positions upsert (only advance ts), positions history insert,
        # heartbeat - the same shape the primary ingester writes
        lp = pg_insert(LatestPosition).values(vals)
        await session.execute(lp.on_conflict_do_update(
            index_elements=["mmsi"],
            set_={c: getattr(lp.excluded, c) for c in
                  ("ts", "lat", "lon", "sog", "cog", "heading",
                   "nav_status", "ship_name", "source")},
            where=lp.excluded.ts > LatestPosition.ts))
        await session.execute(pg_insert(Position).values(vals))
        minute = now.replace(second=0, microsecond=0)
        await session.execute(pg_insert(IngestHeartbeat)
                              .values(ts=minute, n=len(vals))
                              .on_conflict_do_update(
                                  index_elements=["ts"],
                                  set_={"n": func.coalesce(IngestHeartbeat.n, 0) + len(vals)}))
        await session.commit()
        logger.info("VesselAPI failover: wrote %d positions across %d regions "
                    "(primary feed stale)", len(vals), len(regions))
