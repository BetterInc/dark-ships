"""AISstream.io ingestion.

A single WebSocket connection (world-feed mode): a global bounding box that
receives all terrestrially received traffic. We now persist history for EVERY
ship uniformly at 1/min - in-region traffic tagged 'region', the rest 'world' -
so any vessel can be located and tracked after the fact, and a user following a
ship is served from our own recorded data (no dedicated provider slot).
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

import websockets
from sqlalchemy import insert

from ..config import get_settings
from ..db import SessionLocal
from ..models import IngestHeartbeat, LatestPosition, Position
from .registry import RegistryTracker

logger = logging.getLogger(__name__)

AISSTREAM_URL = "wss://stream.aisstream.io/v0/stream"
GLOBAL_BBOX = [[[-90.0, -180.0], [90.0, 180.0]]]
FLUSH_INTERVAL_SECONDS = 2.0
SNAPSHOT_MAINTENANCE_SECONDS = 60

# Class A transponders (large commercial vessels) send PositionReport;
# Class B (fishing boats, yachts, small workboats) send the two ClassB
# variants and StaticDataReport instead - without these, small ships are
# invisible to the ingest.
POSITION_MESSAGE_TYPES = (
    "PositionReport",
    "StandardClassBPositionReport",
    "ExtendedClassBPositionReport",
)
SUBSCRIBED_MESSAGE_TYPES = [*POSITION_MESSAGE_TYPES, "ShipStaticData", "StaticDataReport"]

# World-feed mode: live positions of every ship on the planet (terrestrially
# received), kept in memory for the map's ambient layer. History goes to
# Postgres for every ship (throttled per vessel).
world_snapshot: dict[int, dict] = {}


def get_world_snapshot(max_age_minutes: int = 30) -> list[dict]:
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
    return [p for p in world_snapshot.values() if p["ts"] >= cutoff]


def _in_any_region(lat: float, lon: float, bboxes: list) -> bool:
    for (lat_min, lon_min), (lat_max, lon_max) in bboxes:
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return True
    return False


def _parse_time_utc(raw: str) -> datetime:
    # Voorbeeld: "2022-12-29 18:22:32.318353 +0000 UTC"
    cleaned = raw.replace(" UTC", "")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f %z", "%Y-%m-%d %H:%M:%S %z"):
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return datetime.now(timezone.utc)


def _clean(value: float | None, not_available: float) -> float | None:
    if value is None or value == not_available:
        return None
    return value


class PositionBuffer:
    """Batches inserts so a busy region feed doesn't hit Postgres row by row.
    Also writes a per-minute ingest heartbeat while messages are flowing, so
    gap rules can distinguish 'ship went dark' from 'we were not listening'."""

    def __init__(self):
        self._rows: list[dict] = []
        self._lock = asyncio.Lock()
        self._last_heartbeat: datetime | None = None

    async def add(self, row: dict) -> None:
        async with self._lock:
            self._rows.append(row)

    async def flush_forever(self) -> None:
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        while True:
            await asyncio.sleep(FLUSH_INTERVAL_SECONDS)
            async with self._lock:
                rows, self._rows = self._rows, []
            if not rows:
                continue
            try:
                async with SessionLocal() as session:
                    await session.execute(insert(Position), rows)
                    # maintain the one-row-per-ship current picture: the API's
                    # "where is everything now" reads this instead of scanning
                    # position history
                    newest: dict[int, dict] = {}
                    for r in rows:
                        cur = newest.get(r["mmsi"])
                        if cur is None or r["ts"] > cur["ts"]:
                            newest[r["mmsi"]] = r
                    from sqlalchemy.dialects.postgresql import insert as pg_ins
                    stmt = pg_ins(LatestPosition).values(list(newest.values()))
                    await session.execute(stmt.on_conflict_do_update(
                        index_elements=["mmsi"],
                        set_={c: getattr(stmt.excluded, c)
                              for c in ("ts", "lat", "lon", "sog", "cog",
                                        "heading", "nav_status", "ship_name", "source")},
                        where=stmt.excluded.ts > LatestPosition.ts,
                    ))
                    now = datetime.now(timezone.utc)
                    minute = now.replace(second=0, microsecond=0)
                    if self._last_heartbeat != minute:
                        await session.execute(
                            pg_insert(IngestHeartbeat).values(ts=minute)
                            .on_conflict_do_nothing(index_elements=["ts"])
                        )
                        self._last_heartbeat = minute
                    await session.commit()
            except Exception:
                logger.exception("Failed to write %d positions", len(rows))


class AisStreamConnection:
    def __init__(self, label: str, api_key: str, source: str, buffer: PositionBuffer,
                 registry: RegistryTracker, throttle_seconds: int = 0):
        self.label = label
        self.api_key = api_key
        self.source = source
        self.buffer = buffer
        self.registry = registry
        self.throttle_seconds = throttle_seconds
        # coarser throttle for out-of-region world traffic (set by RegionsConnection)
        self.world_throttle_seconds = throttle_seconds
        self.world_mode = False
        self.region_bboxes: list = []
        self._last_seen: dict[int, float] = {}
        self.resubscribe = asyncio.Event()

    async def _subscription(self) -> dict:
        raise NotImplementedError

    async def run(self) -> None:
        if not self.api_key:
            logger.warning("[%s] no API key configured - connection skipped", self.label)
            return
        backoff = 1
        while True:
            try:
                sub = await self._subscription()
                if sub is None:
                    await asyncio.sleep(SNAPSHOT_MAINTENANCE_SECONDS)
                    continue
                async with websockets.connect(AISSTREAM_URL) as ws:
                    # The subscription must arrive within 3s of connecting
                    await ws.send(json.dumps(sub))
                    logger.info("[%s] connected and subscribed", self.label)
                    backoff = 1
                    self.resubscribe.clear()
                    await self._consume(ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("[%s] connection lost (%s) - retrying in %ds", self.label, exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _consume(self, ws) -> None:
        loop = asyncio.get_running_loop()
        while True:
            if self.resubscribe.is_set():
                logger.info("[%s] subscription outdated - reconnecting", self.label)
                return
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            msg_type = msg.get("MessageType")
            meta = msg.get("Metadata") or {}

            if msg_type == "ShipStaticData":
                static = (msg.get("Message") or {}).get("ShipStaticData") or {}
                mmsi = meta.get("MMSI") or static.get("UserID")
                if mmsi:
                    from .registry import valid_imo
                    imo = str(static.get("ImoNumber") or "")
                    await self.registry.observe_static(
                        mmsi=mmsi,
                        ts=_parse_time_utc(meta.get("time_utc", "")),
                        name=(static.get("Name") or meta.get("ShipName") or "").strip() or None,
                        imo=imo if valid_imo(imo) else None,
                        callsign=(static.get("CallSign") or "").strip() or None,
                        ship_type=static.get("Type"),
                        destination=static.get("Destination"),
                        draught=static.get("MaximumStaticDraught"),
                    )
                continue

            if msg_type == "StaticDataReport":
                # Class B static info arrives in two alternating parts:
                # ReportA carries the name, ReportB callsign/type. No IMO,
                # destination or draught on Class B.
                sdr = (msg.get("Message") or {}).get("StaticDataReport") or {}
                mmsi = meta.get("MMSI") or sdr.get("UserID")
                if mmsi:
                    part_a = sdr.get("ReportA") or {}
                    part_b = sdr.get("ReportB") or {}
                    await self.registry.observe_static(
                        mmsi=mmsi,
                        ts=_parse_time_utc(meta.get("time_utc", "")),
                        name=(part_a.get("Name") or meta.get("ShipName") or "").strip() or None,
                        imo=None,
                        callsign=(part_b.get("CallSign") or "").strip() or None,
                        ship_type=part_b.get("ShipType"),
                        destination=None,
                        draught=None,
                    )
                continue

            if msg_type not in POSITION_MESSAGE_TYPES:
                continue
            # Class B reports share the Class A field names for everything we
            # store; fields a variant lacks (e.g. NavigationalStatus) just
            # .get() to None.
            report = (msg.get("Message") or {}).get(msg_type) or {}
            mmsi = meta.get("MMSI") or report.get("UserID")
            if not mmsi:
                continue

            ts = _parse_time_utc(meta.get("time_utc", ""))
            lat = report.get("Latitude", meta.get("latitude"))
            lon = report.get("Longitude", meta.get("longitude"))
            if lat is None or lon is None:
                continue
            # "Null Island" (0,0) and out-of-range values are a GPS-no-fix /
            # unconfigured-transponder default, not a real position - drop them
            # so we never store or plot a ghost ship off the coast of Africa.
            if (abs(lat) < 0.0001 and abs(lon) < 0.0001) or abs(lat) > 90 or abs(lon) > 180:
                continue
            ship_name = (meta.get("ShipName") or "").strip() or None
            await self.registry.observe_position(mmsi, ts, lat, lon, ship_name)

            in_region = _in_any_region(lat, lon, self.region_bboxes)
            if self.world_mode:
                world_snapshot[mmsi] = {
                    "mmsi": mmsi, "ts": ts, "lat": lat, "lon": lon,
                    "sog": _clean(report.get("Sog"), 102.3),
                    "cog": _clean(report.get("Cog"), 360.0),
                    "ship_name": ship_name,
                }

            # We persist history for EVERY ship uniformly, so any vessel can be
            # located and its track reconstructed after the fact. Throttle per
            # ship: the curated in-region feed stays fine (1/min) for the
            # detectors, the wider world feed is stored at the same cadence.
            # In-region traffic is tagged 'region', the rest 'world'; retention
            # prunes ambient history after 90 days.
            throttle = self.throttle_seconds if in_region else self.world_throttle_seconds
            if throttle:
                now = loop.time()
                last = self._last_seen.get(mmsi)
                if last is not None and now - last < throttle:
                    continue
                self._last_seen[mmsi] = now

            source = self.source if in_region else "world"
            await self.buffer.add({
                "mmsi": mmsi,
                "ts": ts,
                "lat": lat,
                "lon": lon,
                "sog": _clean(report.get("Sog"), 102.3),
                "cog": _clean(report.get("Cog"), 360.0),
                "heading": _clean(report.get("TrueHeading"), 511),
                "nav_status": report.get("NavigationalStatus"),
                "ship_name": ship_name,
                "source": source,
            })


class RegionsConnection(AisStreamConnection):
    def __init__(self, api_key: str, buffer: PositionBuffer, registry: RegistryTracker):
        settings = get_settings()
        label = "world" if settings.ais_world_feed else "regions"
        super().__init__(label, api_key, "region", buffer, registry,
                         throttle_seconds=settings.region_throttle_seconds)
        self.world_throttle_seconds = settings.world_throttle_seconds
        self.world_mode = settings.ais_world_feed
        self.region_bboxes = [region.bbox for region in settings.ais_regions]

    async def _subscription(self) -> dict | None:
        if self.world_mode:
            return {
                "APIKey": self.api_key,
                "BoundingBoxes": GLOBAL_BBOX,
                "FilterMessageTypes": SUBSCRIBED_MESSAGE_TYPES,
            }
        if not self.region_bboxes:
            logger.info("[regions] no regions configured (AIS_REGIONS) - connection skipped")
            return None
        return {
            "APIKey": self.api_key,
            "BoundingBoxes": self.region_bboxes,
            "FilterMessageTypes": SUBSCRIBED_MESSAGE_TYPES,
        }


async def _start_with_preload(registry: RegistryTracker) -> None:
    try:
        await registry.preload_known()
    except Exception:
        logger.exception("Registry preload failed")
    await registry.flush_forever()


async def _prune_world_snapshot() -> None:
    """Prunes stale world-snapshot entries so the in-memory ambient layer stays
    bounded."""
    from datetime import timedelta
    while True:
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
            stale = [m for m, p in world_snapshot.items() if p["ts"] < cutoff]
            for m in stale:
                del world_snapshot[m]
            if stale:
                logger.info("World snapshot: pruned %d stale ships", len(stale))
        except Exception:
            logger.exception("World snapshot prune failed")
        await asyncio.sleep(SNAPSHOT_MAINTENANCE_SECONDS)


def start_ingest(loop_tasks: list[asyncio.Task]) -> None:
    settings = get_settings()
    buffer = PositionBuffer()
    registry = RegistryTracker()
    loop_tasks.append(asyncio.create_task(buffer.flush_forever(), name="ais-flush"))
    loop_tasks.append(asyncio.create_task(_start_with_preload(registry), name="registry-flush"))
    loop_tasks.append(asyncio.create_task(_prune_world_snapshot(), name="world-snapshot-prune"))

    # One ingestion path: a single world-mode connection records every ship at
    # 1/min (region + world). Following a ship is served from this recorded data.
    regions = RegionsConnection(settings.aisstream_api_key_regions, buffer, registry)
    loop_tasks.append(asyncio.create_task(regions.run(), name="ais-regions"))
