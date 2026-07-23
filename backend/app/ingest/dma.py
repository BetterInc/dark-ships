"""Denmark DMA (Danish Maritime Authority) historical AIS - a batch enrichment
source feeding the one unified position stream.

Unlike the streaming feeds (aisstream/Digitraffic/Kystverket), DMA is a daily
BULK archive: one CSV-in-ZIP per day at the public S3 bucket, ~3 days behind
real time, ~700 MB-1 GB zipped. It covers Danish / SW-Baltic waters (Oresund,
Great Belt, Kattegat) - prime shadow-fleet chokepoints - so it enriches vessel
TRACK HISTORY there, especially for coastal/fishing traffic the terrestrial
firehose misses.

It writes to the SAME positions / latest_positions tables as every other
source (tagged source='dma'), so the map, track history and detectors pick it
up with no special-casing. Two deliberate differences from the live feeds:

  1. It inserts EVERY filtered fix for the day (the whole track), not just each
     ship's newest one - the point is to backfill history, not a live snapshot.
  2. It writes NO ingest heartbeat. Heartbeats mark when we were provably
     listening (covered_since guard, jobs/coverage.py); a 3-day-old backfill
     was not "listening then", and faking it would suppress real gap detection.

Idempotent: re-importing a day replaces that day's source='dma' rows for the
selected vessels first, so re-runs don't duplicate history.

Licensing: DMA AIS is free open data. Verify the current DMA data-management
policy permits redistribution before RE-SERVING it from a commercial product
(ingesting it into our own analysis is fine); the national feeds we re-serve
today are NLOD/CC-BY. See [[ais-source-strategy]].
"""

import asyncio
import contextlib
import io
import logging
import os
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import and_, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..config import get_settings
from ..db import SessionLocal
from ..models import LatestPosition, Position, Vessel

logger = logging.getLogger(__name__)

# Public S3 bucket (see http://aisdata.ais.dk/). Recent days live at the root as
# aisdk-YYYY-MM-DD.zip; older data is under year-prefixed monthly zips.
DMA_BUCKET = "http://aisdata.ais.dk.s3.eu-central-1.amazonaws.com"
SOURCE = "dma"
# Newest file is ~3 days behind; default the daily job to that lag.
DEFAULT_LAG_DAYS = 3
SOG_MAX_PLAUSIBLE_KN = 60.0  # match the live ingesters' garbled-speed clamp
# Position insert has 10 columns; asyncpg caps a statement at 32767 params, so
# keep chunks * 10 well under that.
_INSERT_CHUNK = 3000

# DMA CSV column order (26 cols, comma-delimited, decimal points, header line
# begins "# Timestamp"). We only consume the handful we store.
_COL = {"ts": 0, "mobile": 1, "mmsi": 2, "lat": 3, "lon": 4, "navstat": 5,
        "sog": 7, "cog": 8, "heading": 9, "name": 12}

# DMA navigational-status text -> AIS numeric code (our schema stores the int).
# Unmapped / "Unknown value" / blank -> None.
_NAV_STATUS = {
    "under way using engine": 0, "at anchor": 1, "not under command": 2,
    "restricted manoeuverability": 3, "restricted manoeuvrability": 3,
    "constrained by her draught": 4, "moored": 5, "aground": 6,
    "engaged in fishing": 7, "under way sailing": 8,
    "ais-sart is active": 14, "ais-sart": 14,
}


def _f(v: str) -> float | None:
    v = v.strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _parse_ts(v: str) -> datetime | None:
    # DMA timestamps are UTC, format dd/mm/YYYY HH:MM:SS
    try:
        return datetime.strptime(v.strip(), "%d/%m/%Y %H:%M:%S").replace(
            tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def _row(fields: list[str]) -> dict | None:
    """Map one DMA CSV row to our position dict, or None to skip."""
    if len(fields) <= _COL["name"]:
        return None
    if not fields[_COL["mobile"]].startswith("Class"):
        return None  # keep only Class A/B vessels; drop base stations, AtoN, etc.
    try:
        mmsi = int(fields[_COL["mmsi"]])
    except ValueError:
        return None
    lat, lon = _f(fields[_COL["lat"]]), _f(fields[_COL["lon"]])
    if lat is None or lon is None:
        return None
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return None
    if lat == 0.0 and lon == 0.0:
        return None  # null island
    ts = _parse_ts(fields[_COL["ts"]])
    if ts is None:
        return None
    sog = _f(fields[_COL["sog"]])
    if sog is not None and sog > SOG_MAX_PLAUSIBLE_KN:
        sog = None  # garbled speed; keep the position
    name = fields[_COL["name"]].strip() or None
    return {
        "mmsi": mmsi, "ts": ts, "lat": lat, "lon": lon, "sog": sog,
        "cog": _f(fields[_COL["cog"]]), "heading": _f(fields[_COL["heading"]]),
        "nav_status": _NAV_STATUS.get(fields[_COL["navstat"]].strip().lower()),
        "ship_name": name[:128] if name else None, "source": SOURCE,
    }


def _in_bbox(row: dict, bbox: tuple[float, float, float, float]) -> bool:
    lat1, lon1, lat2, lon2 = bbox
    return min(lat1, lat2) <= row["lat"] <= max(lat1, lat2) and \
        min(lon1, lon2) <= row["lon"] <= max(lon1, lon2)


def _parse_csv(fileobj, mmsis: set[int] | None,
               bbox: tuple[float, float, float, float] | None):
    """Stream a DMA CSV, filter by mmsi set and/or bbox, and return
    (all_points, newest_by_mmsi). all_points backfills history; newest_by drives
    the advance-only latest_positions upsert.

    A day is ~15-25M rows and we usually keep only the watchlist subset, so the
    hot loop must be cheap. We split on ',' and, for the common MMSI-filtered
    case, test the MMSI (field 2) BEFORE doing any per-row work - skipping ~all
    lines with a single split+set lookup. csv.reader here was ~10-30x slower and
    pegged the 1-CPU worker for minutes per day. Fields 0-9 and 12 (all we read)
    precede the free-text Name/Destination, and fields 0-11 never contain commas
    (numbers and fixed enums), so a naive split is safe for the values we use."""
    mmsi_strs = {str(m) for m in mmsis} if mmsis is not None else None
    all_points: list[dict] = []
    newest_by: dict[int, dict] = {}
    first = True
    for line in fileobj:
        if first:
            first = False
            if line.lstrip().startswith("# Timestamp"):
                continue  # header line
        if mmsi_strs is not None:
            # cheap pre-filter: MMSI is the 3rd field
            head = line.split(",", 3)
            if len(head) < 3 or head[2] not in mmsi_strs:
                continue
        row = _row(line.rstrip("\r\n").split(","))
        if row is None:
            continue
        if bbox is not None and not _in_bbox(row, bbox):
            continue
        all_points.append(row)
        cur = newest_by.get(row["mmsi"])
        if cur is None or row["ts"] > cur["ts"]:
            newest_by[row["mmsi"]] = row
    return all_points, newest_by


async def _tracked_mmsis() -> set[int]:
    """Active watchlist vessels - the default enrichment target."""
    async with SessionLocal() as session:
        rows = await session.execute(select(Vessel.mmsi).where(Vessel.active.is_(True)))
        return {m for (m,) in rows.all()}


async def _write(all_points: list[dict], newest_by: dict[int, dict],
                 day: datetime) -> int:
    if not all_points:
        return 0
    mmsis = list(newest_by.keys())
    day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    async with SessionLocal() as session:
        # idempotent: drop this day's prior DMA rows for these ships, then insert
        await session.execute(delete(Position).where(and_(
            Position.source == SOURCE, Position.mmsi.in_(mmsis),
            Position.ts >= day_start, Position.ts < day_end)))
        for i in range(0, len(all_points), _INSERT_CHUNK):
            await session.execute(pg_insert(Position).values(
                all_points[i:i + _INSERT_CHUNK]))
        # advance-only latest_positions: DMA only wins a ship's live row if its
        # newest fix is genuinely newer than what a live feed already has.
        existing = dict((await session.execute(
            select(LatestPosition.mmsi, LatestPosition.ts)
            .where(LatestPosition.mmsi.in_(mmsis)))).all())
        fresh = [r for m, r in newest_by.items()
                 if m not in existing or r["ts"] > existing[m]]
        if fresh:
            lp = pg_insert(LatestPosition).values(fresh)
            await session.execute(lp.on_conflict_do_update(
                index_elements=["mmsi"],
                set_={c: getattr(lp.excluded, c) for c in
                      ("ts", "lat", "lon", "sog", "cog", "heading",
                       "nav_status", "ship_name", "source")},
                where=lp.excluded.ts > LatestPosition.ts))
        await session.commit()
    logger.info("DMA %s: wrote %d history points, advanced %d latest positions "
                "(%d vessels)", day_start.date(), len(all_points), len(fresh),
                len(newest_by))
    return len(all_points)


@contextlib.contextmanager
def _download_csv(date_str: str):
    """Stream aisdk-<date>.zip to a temp file and yield a text stream over its
    single CSV entry. Streaming to disk (not BytesIO) keeps memory flat and
    small - the daily zip is ~700 MB-1 GB and the worker pod has a 2 GB limit,
    so buffering the whole file in RAM would risk an OOM. Kept sync (blocking
    IO) - callers hop to a thread."""
    url = f"{DMA_BUCKET}/aisdk-{date_str}.zip"
    logger.info("DMA: downloading %s", url)
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    try:
        with httpx.stream("GET", url, timeout=600.0, follow_redirects=True) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_bytes(1 << 20):
                tmp.write(chunk)
        tmp.close()
        with zipfile.ZipFile(tmp.name) as zf:
            name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
            with zf.open(name) as raw:
                yield io.TextIOWrapper(raw, encoding="latin-1", newline="")
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def _download_and_parse(date_str: str, mmsis, bbox):
    with _download_csv(date_str) as fileobj:
        return _parse_csv(fileobj, mmsis, bbox)


async def import_dma_day(
    date_str: str,
    mmsis: set[int] | None = None,
    bbox: tuple[float, float, float, float] | None = None,
) -> int:
    """Import one DMA day (YYYY-MM-DD) into the unified stream.

    Filters to `mmsis` and/or `bbox`. If NEITHER is given, defaults to the
    active watchlist (a whole unfiltered day is millions of rows - never
    ingested wholesale). Returns the number of history points written.
    """
    if mmsis is None and bbox is None:
        mmsis = await _tracked_mmsis()
        if not mmsis:
            logger.warning("DMA %s: no watchlist vessels and no filter - skipping",
                           date_str)
            return 0
    day = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    all_points, newest_by = await asyncio.to_thread(
        _download_and_parse, date_str, mmsis, bbox)
    return await _write(all_points, newest_by, day)


async def run_dma_backfill() -> None:
    """Scheduled daily job: pull the newest available DMA day (~3 days behind),
    enriching the active watchlist. Gated on config.dma_enabled."""
    s = get_settings()
    if not s.dma_enabled:
        return
    date_str = (datetime.now(timezone.utc) - timedelta(days=DEFAULT_LAG_DAYS)
                ).strftime("%Y-%m-%d")
    try:
        await import_dma_day(date_str)
    except Exception:
        logger.exception("DMA backfill failed for %s", date_str)


# CLI: python -m app.ingest.dma --date 2026-07-20 [--days N] [--mmsi a,b]
#      [--bbox lat1,lon1,lat2,lon2]
if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Import Denmark DMA historical AIS")
    p.add_argument("--date", required=True, help="start date YYYY-MM-DD")
    p.add_argument("--days", type=int, default=1, help="number of days from --date")
    p.add_argument("--mmsi", help="comma-separated MMSIs to filter (default: watchlist)")
    p.add_argument("--bbox", help="lat1,lon1,lat2,lon2 bounding box filter")
    args = p.parse_args()

    mmsis = {int(x) for x in args.mmsi.split(",")} if args.mmsi else None
    bbox = tuple(float(x) for x in args.bbox.split(",")) if args.bbox else None
    start = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    async def _main():
        total = 0
        for i in range(args.days):
            d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
            total += await import_dma_day(d, mmsis, bbox)
        logger.info("DMA import complete: %d points across %d day(s)", total, args.days)

    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())
