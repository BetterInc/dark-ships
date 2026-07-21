"""Raw AIVDM/NMEA TCP feeds - free national AIS sources feeding the one stream.

Several coastal agencies broadcast their AIS reception as an anonymous TCP
stream of AIVDM sentences (no key). Norway's Kystverket (153.44.253.27:5631) is
one such: real-time terrestrial AIS along the Norwegian coast and North Sea.
This module is a generic runner - point it at (host, port, source) and it feeds
the same latest_positions / positions tables as every other provider.

We decode only single-fragment sentences, which carry ALL position report types
(1/2/3 Class A, 18/19 Class B). Multi-part sentences are static/voyage data
(names) that the registry already provides, so reassembly is unnecessary. The
NMEA tag block's `c:<epoch>` gives each fix its real receive time, so the
advance-only write (below) dedups stationary ships and any overlap with other
feeds against one source of truth (latest_positions) - the history stays clean.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from pyais import decode as ais_decode
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..db import SessionLocal
from ..models import IngestHeartbeat, LatestPosition, Position

logger = logging.getLogger(__name__)

FLUSH_SECONDS = 30
MAX_AGE = timedelta(hours=6)         # ignore fixes stale on arrival
POSITION_TYPES = {1, 2, 3, 18, 19}
READ_TIMEOUT = 90                    # a silent socket this long = dead, reconnect


def _tag_epoch_and_sentence(line: str) -> tuple[datetime | None, str]:
    """Split an optional NMEA tag block off the front and read its `c:` receive
    time. `\\s:station,c:1784637253*hh\\!BSVDM,...` -> (ts, '!BSVDM,...')."""
    ts = None
    if line.startswith("\\"):
        end = line.find("\\", 1)
        if end > 0:
            for kv in line[1:end].split(","):
                if kv.startswith("c:"):
                    try:
                        ts = datetime.fromtimestamp(int(kv[2:].split("*")[0]), tz=timezone.utc)
                    except (ValueError, OSError):
                        ts = None
            line = line[end + 1:]
    return ts, line


def _row_from_sentence(line: str, source: str, now: datetime) -> dict | None:
    ts, sentence = _tag_epoch_and_sentence(line)
    i = sentence.find("!")
    if i < 0:
        return None
    sentence = sentence[i:]
    # single-fragment only (field 1 == total fragments); positions always fit one
    parts = sentence.split(",")
    if len(parts) < 7 or parts[1] != "1":
        return None
    try:
        m = ais_decode(sentence)
    except Exception:
        return None
    if getattr(m, "msg_type", None) not in POSITION_TYPES:
        return None
    lat, lon = getattr(m, "lat", None), getattr(m, "lon", None)
    if lat is None or lon is None or abs(lat) > 90 or abs(lon) > 180:
        return None  # 91/181 = "not available"
    ts = ts or now
    if ts < now - MAX_AGE or ts > now + timedelta(minutes=5):
        return None
    sog, cog = getattr(m, "speed", None), getattr(m, "course", None)
    heading = getattr(m, "heading", None)
    return {
        "mmsi": int(m.mmsi), "ts": ts,
        "lat": round(float(lat), 5), "lon": round(float(lon), 5),
        "sog": float(sog) if sog is not None and sog < 102.3 else None,
        "cog": float(cog) if cog is not None and cog < 360 else None,
        "heading": int(heading) if heading is not None and heading != 511 else None,
        "nav_status": int(m.status) if getattr(m, "status", None) is not None else None,
        "ship_name": None,   # names come from the registry, not the position feed
        "source": source,
    }


class _AivdmBuffer:
    """Newest fix per mmsi since the last flush, written advance-only."""

    def __init__(self, source: str):
        self.source = source
        self._newest: dict[int, dict] = {}
        self._lock = asyncio.Lock()

    async def add(self, row: dict) -> None:
        async with self._lock:
            cur = self._newest.get(row["mmsi"])
            if cur is None or row["ts"] > cur["ts"]:
                self._newest[row["mmsi"]] = row

    async def flush_forever(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_SECONDS)
            async with self._lock:
                batch, self._newest = self._newest, {}
            if not batch:
                continue
            try:
                await self._write(batch)
            except Exception:
                logger.exception("[%s] flush failed (%d rows)", self.source, len(batch))

    async def _write(self, batch: dict[int, dict]) -> None:
        async with SessionLocal() as session:
            # advance-only: only fixes newer than a ship's stored last-known ts,
            # so stationary ships and overlap with other feeds don't duplicate.
            existing = dict((await session.execute(
                select(LatestPosition.mmsi, LatestPosition.ts)
                .where(LatestPosition.mmsi.in_(list(batch))))).all())
            fresh = [r for m, r in batch.items()
                     if m not in existing or r["ts"] > existing[m]]
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
            minute = datetime.now(timezone.utc).replace(second=0, microsecond=0)
            await session.execute(pg_insert(IngestHeartbeat)
                                  .values(ts=minute, n=len(fresh))
                                  .on_conflict_do_update(
                                      index_elements=["ts"],
                                      set_={"n": func.coalesce(IngestHeartbeat.n, 0) + len(fresh)}))
            await session.commit()
            logger.info("[%s] wrote %d fresh positions (%d in window)",
                        self.source, len(fresh), len(batch))


async def run_aivdm_feed(name: str, host: str, port: int, source: str) -> None:
    """Persistent reader: connect, decode positions into a buffer, reconnect on
    drop with backoff. Runs forever (launched as a task, like the aisstream
    connection). The buffer's flush loop writes to the stream every 30s."""
    buffer = _AivdmBuffer(source)
    asyncio.create_task(buffer.flush_forever(), name=f"{name}-flush")
    backoff = 1
    while True:
        try:
            reader, writer = await asyncio.open_connection(host, port)
            logger.info("[%s] connected %s:%d", name, host, port)
            backoff = 1
            while True:
                raw = await asyncio.wait_for(reader.readline(), timeout=READ_TIMEOUT)
                if not raw:
                    raise ConnectionError("stream closed")
                line = raw.decode("ascii", "ignore").strip()
                if not line:
                    continue
                row = _row_from_sentence(line, source, datetime.now(timezone.utc))
                if row:
                    await buffer.add(row)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[%s] connection lost (%s) - retrying in %ds", name, exc, backoff)
            try:
                writer.close()
            except Exception:
                pass
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
