import json
import asyncio
import gzip
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session
from ..models import LatestPosition, Position, PositionCheck, RiskEvent, Vessel, VesselRegistry
from ..schemas import LatestPositionOut, RegionOut

# The latest-positions snapshot is identical for every visitor and costs ~1s of
# DB work + a few MB to build. Cache the finished JSON for a few seconds so 10k
# concurrent pollers trigger ONE rebuild per window, not one per request. We cache
# BOTH raw and pre-gzipped bytes: browsers accept gzip, and compressing a 4 MB body
# per request would just move the bottleneck from the DB onto the CPU.
# The lock stops a cache-miss stampede (only one request rebuilds; the rest wait).
_LATEST_TTL = 15.0
_latest_cache: dict[float, tuple[float, bytes, bytes]] = {}  # since_hours -> (ts, raw, gz)
_latest_lock = asyncio.Lock()
_latest_adapter = TypeAdapter(list[LatestPositionOut])
# lets a CDN / reverse proxy serve the shared snapshot to every viewer, so the
# origin handles ~1 request per window regardless of how many people are watching
_CACHE_HEADERS = {"Cache-Control": "public, max-age=15", "Vary": "Accept-Encoding"}


def _latest_response(request: Request, raw: bytes, gz: bytes) -> Response:
    """Serve the pre-gzipped bytes to clients that accept gzip (all browsers),
    raw otherwise - never re-compressing per request."""
    if "gzip" in request.headers.get("accept-encoding", ""):
        return Response(gz, media_type="application/json",
                        headers={**_CACHE_HEADERS, "Content-Encoding": "gzip"})
    return Response(raw, media_type="application/json", headers=_CACHE_HEADERS)


def ship_type_label(code: int | None) -> str | None:
    """AIS ship-type code -> human label (tanker, cargo, fishing, ...)."""
    if code is None:
        return None
    if 80 <= code <= 89:
        return "tanker"
    if 70 <= code <= 79:
        return "cargo"
    if 60 <= code <= 69:
        return "passenger"
    if 40 <= code <= 49:
        return "high-speed craft"
    if code == 30:
        return "fishing"
    if code in (31, 32, 52):
        return "tug / tow"
    if code == 33:
        return "dredger"
    if code == 35:
        return "military"
    if code in (36, 37):
        return "sailing / pleasure"
    if 50 <= code <= 59:
        return "special craft"
    return "other"

# human-readable name for each detected pattern (mirrors the frontend labels)
PATTERN_LABELS = {
    "regional_gap": "went dark in coverage",
    "identity_change": "identity change",
    "midsea_appearance": "appeared mid-sea",
    "impossible_jump": "impossible jump (spoofing)",
    "rendezvous": "ship-to-ship rendezvous",
    "drift_mismatch": "reappeared off predicted drift",
    "oil_slick": "oil slick at transfer point",
    "dark_association": "anchored among sanctioned ships",
    "loitering": "loitering in a trafficking corridor",
    "mmsi_collision": "one identity in two places",
    "circle_spoofing": "GPS circle-spoofing",
    "gps_jamming_zone": "GPS jamming zone (likely victim)",
    "identity_integrity": "fabricated identity (bad MMSI/IMO)",
    "flag_hop": "reflagged (one IMO, many MMSIs)",
    "nav_status_lie": "claims anchored while moving",
    "gfw_encounter": "met another vessel at sea (transshipment)",
    "gfw_ais_gap": "disabled AIS (went dark)",
    "gfw_loitering": "loitered offshore",
    "draught_change": "changed draught at sea (possible cargo transfer)",
    "risklist_ofac": "OFAC sanctions list",
    "risklist_gur": "Ukraine GUR shadow-fleet list",
    "risklist_eu": "EU port ban",
    "risklist_uk": "UK sanctions list",
    "risklist_uani": "UANI Iran-tanker list",
    "risklist_canada": "Canada sanctions list",
    "risklist_australia": "Australia sanctions list",
    "risklist_switzerland": "Switzerland sanctions list",
    "risklist_un1718": "UN (DPRK) sanctions list",
    "risklist_nz": "New Zealand sanctions list",
    "risklist_iuu": "IUU illegal-fishing blacklist",
    "risklist_iccat": "ICCAT IUU fishing blacklist",
    "risklist_wcpfc": "WCPFC IUU fishing blacklist",
    "risklist_eu_iuu": "EU IUU fishing list",
    "risklist_kse": "KSE tanker tracker",
    "risklist_parismou": "banned from EU ports (Paris MoU)",
    "risklist_tokyomou": "detained by port control (Tokyo MoU)",
    "risklist_blackseamou": "detained by port control (Black Sea MoU)",
    "risklist_abujamou": "detained by port control (Abuja MoU)",
}

router = APIRouter(prefix="/api", tags=["positions"])


@router.get("/feed/status")
async def feed_status(session: AsyncSession = Depends(get_session)):
    """Ingest health for the map's stale-feed banner: how old the freshest
    received position is. `live` false = the upstream AIS feed has stalled and
    the map is showing last-known positions."""
    newest = await session.scalar(select(func.max(LatestPosition.ts)))
    age = None
    if newest is not None:
        age = int((datetime.now(timezone.utc) - newest).total_seconds())
    return Response(
        json.dumps({"newest": newest.isoformat() if newest else None,
                    "age_seconds": age,
                    "live": age is not None and age < 2 * 3600}),
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=60"})


async def _rebuild_latest(since_hours: float) -> tuple[bytes, bytes, bytes, bytes]:
    from ..db import SessionLocal
    async with SessionLocal() as session:
        rows = await _latest_rows(session, since_hours)
    raw = _serialize_latest_json(rows)
    bin_raw = _build_latest_bin(rows)
    # compressed once per window; the binary gzips too (text tail + columns)
    gz = gzip.compress(raw, compresslevel=6)
    bin_gz = gzip.compress(bin_raw, compresslevel=6)
    _latest_cache[since_hours] = (time.monotonic(), raw, gz, bin_raw, bin_gz)
    return raw, gz, bin_raw, bin_gz


async def _refresh_latest_bg(since_hours: float) -> None:
    async with _latest_lock:
        cached = _latest_cache.get(since_hours)
        if cached and time.monotonic() - cached[0] < _LATEST_TTL:
            return  # someone else already refreshed
        try:
            await _rebuild_latest(since_hours)
        except Exception:
            pass  # keep serving the stale copy; next request retries


# The cache is keyed by since_hours, and since_hours is a PUBLIC float param:
# without snapping, every distinct value (?since_hours=1.01, 1.02, ...) would
# mint a permanent ~5MB raw+gzip entry - an easy way to OOM the web pods.
_LATEST_WINDOWS = (1.0, 3.0, 6.0, 12.0, 24.0, 48.0, 72.0, 168.0)


@router.get("/positions/latest", response_model=list[LatestPositionOut])
async def latest_positions(request: Request, since_hours: float = Query(24, le=24 * 7)):
    """Most recent position per vessel, incl. watchlist info. Served from a
    short-lived shared cache, stale-while-revalidate: an expired cache is
    served immediately while one background task rebuilds it - no visitor
    ever waits on the rebuild. since_hours snaps to a fixed set of windows
    (see _LATEST_WINDOWS) so the cache stays bounded."""
    since_hours = min(_LATEST_WINDOWS, key=lambda w: abs(w - since_hours))
    cached = _latest_cache.get(since_hours)
    if cached:
        if time.monotonic() - cached[0] >= _LATEST_TTL and not _latest_lock.locked():
            asyncio.get_running_loop().create_task(_refresh_latest_bg(since_hours))
        return _latest_response(request, cached[1], cached[2])
    async with _latest_lock:
        cached = _latest_cache.get(since_hours)  # built while we waited?
        if cached:
            return _latest_response(request, cached[1], cached[2])
        raw, gz, _, _ = await _rebuild_latest(since_hours)
        return _latest_response(request, raw, gz)


@router.get("/positions/latest.bin")
async def latest_positions_bin(request: Request,
                               since_hours: float = Query(24, le=24 * 7)):
    """The same snapshot as /positions/latest in the columnar binary format
    (see _build_latest_bin) - ~5x smaller pre-gzip and decodable in
    milliseconds, which keeps the map's main thread free while 65k+ ships
    refresh. Same shared cache and stale-while-revalidate behaviour."""
    since_hours = min(_LATEST_WINDOWS, key=lambda w: abs(w - since_hours))
    cached = _latest_cache.get(since_hours)
    if cached:
        if time.monotonic() - cached[0] >= _LATEST_TTL and not _latest_lock.locked():
            asyncio.get_running_loop().create_task(_refresh_latest_bg(since_hours))
        return _bin_response(request, cached[3], cached[4])
    async with _latest_lock:
        cached = _latest_cache.get(since_hours)
        if cached:
            return _bin_response(request, cached[3], cached[4])
        _, _, bin_raw, bin_gz = await _rebuild_latest(since_hours)
        return _bin_response(request, bin_raw, bin_gz)


def _bin_response(request: Request, raw: bytes, gz: bytes) -> Response:
    if "gzip" in request.headers.get("accept-encoding", ""):
        return Response(gz, media_type="application/octet-stream",
                        headers={**_CACHE_HEADERS, "Content-Encoding": "gzip"})
    return Response(raw, media_type="application/octet-stream", headers=_CACHE_HEADERS)


async def _latest_rows(session: AsyncSession, since_hours: float) -> list[dict]:
    """Build the current picture from latest_positions (one row per ship,
    maintained by the ingester) - NOT by DISTINCT-ON scanning the position
    history, which grows by millions of rows per day and took >10s."""
    now = datetime.now(timezone.utc)
    # Feed-outage resilience: normally show ships seen in the last `since_hours`.
    # But if the ingest feed has stalled (upstream AIS provider down), the
    # freshest row is hours old and the normal window would be EMPTY - a blank
    # map. In that case anchor the window to the LAST DATA WE HAVE: show the
    # fleet exactly as it was in the `since_hours` before the feed died. Same
    # ~66k size (not a 14-day, 166k-ship pile that renders slowly), and it's
    # the honest "last-known picture" - real positions, never extrapolated.
    newest = await session.scalar(select(func.max(LatestPosition.ts)))
    stale = newest is not None and (now - newest) > timedelta(hours=2)
    anchor = newest if stale else now
    since = anchor - timedelta(hours=since_hours)
    result = await session.execute(
        # __table__ expands to flat columns (like the old subquery select did),
        # so dict(r._mapping) below keeps its shape
        select(LatestPosition.__table__, Vessel.category, Vessel.name.label("vessel_name"),
               Vessel.risk_score, Vessel.notes, VesselRegistry.ship_type,
               VesselRegistry.name.label("registry_name"))
        .where(LatestPosition.ts >= since)
        # active check in the JOIN: suppressed/removed ships must render as
        # plain regional traffic, not keep their watchlist colors
        .outerjoin(Vessel, (Vessel.mmsi == LatestPosition.mmsi) & Vessel.active.is_(True))
        .outerjoin(VesselRegistry, VesselRegistry.mmsi == LatestPosition.mmsi)
    )
    rows: list[dict] = []
    for r in result:
        d = dict(r._mapping)
        d["ship_type"] = ship_type_label(d.pop("ship_type", None))
        # region ships rarely carry a name on the position row itself; fall back
        # to the name we collected in the registry so the panel isn't just an MMSI
        d["ship_name"] = d.get("ship_name") or d.pop("registry_name", None)
        d.pop("registry_name", None)
        # 5 decimals ~ 1 m; full float repr is 17 chars per coordinate and the
        # payload ships 50k+ rows - precision nobody can see costs real MBs
        # (same for microsecond timestamps: whole seconds are plenty)
        d["ts"] = d["ts"].replace(microsecond=0)
        d["lat"] = round(d["lat"], 5)
        d["lon"] = round(d["lon"], 5)
        if d.get("cog") is not None:
            d["cog"] = round(d["cog"], 1)
        if d.get("heading") is not None:
            d["heading"] = round(d["heading"], 1)
        rows.append(d)

    # attach the specific detected patterns for watchlist ships, so an "other"
    # (behavioural) dot can name exactly which pattern(s) tripped it
    watch_mmsis = [r["mmsi"] for r in rows if r.get("category") is not None]
    patterns: dict[int, list[str]] = {}
    if watch_mmsis:
        for mmsi, rule in await session.execute(
            select(RiskEvent.mmsi, RiskEvent.rule).where(RiskEvent.mmsi.in_(watch_mmsis)).distinct()
        ):
            patterns.setdefault(mmsi, []).append(PATTERN_LABELS.get(rule, rule))
    for r in rows:
        r["patterns"] = patterns.get(r["mmsi"], [])
    return rows


def _serialize_latest_json(rows: list[dict]) -> bytes:
    # exclude_none: most of the 50k+ rows are ambient traffic where category /
    # vessel_name / risk_score / notes etc. are null - dropping the null keys
    # roughly halves the payload (the frontend treats undefined as null)
    # exclude_defaults also drops patterns=[] on the ~50k ambient rows
    return _latest_adapter.dump_json(
        [LatestPositionOut(**r) for r in rows],
        exclude_none=True, exclude_defaults=True)


def _build_latest_bin(rows: list[dict]) -> bytes:
    """Columnar binary snapshot ("DSB1"): the JSON payload is ~188 bytes/ship
    of repeated keys and ISO timestamps for 65k+ ships; this packs the numeric
    core at ~23 bytes/ship (little-endian column arrays) with one small JSON
    tail for the text (names + watchlist enrichment). The frontend worker
    decodes it off the main thread - see frontend/src/map/latestBinary.ts,
    which must mirror this layout exactly.

      "DSB1" | u32 count
      u32 mmsi[]  u32 unix_ts[]  i32 lat*1e5[]  i32 lon*1e5[]
      u16 sog*10[]  u16 cog*10[]  u16 heading*10[]   (0xFFFF = null)
      u8 flags[]  (bit0: source == "region")
      u32 tail_len | tail JSON: {"names": {mmsi: str}, "watch": {mmsi: {...}}}
    """
    import json as _json
    from array import array

    n = len(rows)
    mmsi, ts = array("I"), array("I")
    lat, lon = array("i"), array("i")
    sog, cog, heading = array("H"), array("H"), array("H")
    flags = array("B")
    names: dict[int, str] = {}
    watch: dict[int, dict] = {}
    for r in rows:
        mmsi.append(r["mmsi"])
        ts.append(int(r["ts"].timestamp()))
        lat.append(round(r["lat"] * 1e5))
        lon.append(round(r["lon"] * 1e5))
        sog.append(0xFFFF if r.get("sog") is None else min(0xFFFE, round(r["sog"] * 10)))
        cog.append(0xFFFF if r.get("cog") is None else min(0xFFFE, round(r["cog"] * 10)))
        heading.append(0xFFFF if r.get("heading") is None
                       else min(0xFFFE, round(r["heading"] * 10)))
        flags.append(1 if r.get("source") == "region" else 0)
        if r.get("ship_name"):
            names[r["mmsi"]] = r["ship_name"]
        if r.get("category") is not None:
            watch[r["mmsi"]] = {k: v for k, v in {
                "category": r.get("category"),
                "vessel_name": r.get("vessel_name"),
                "risk_score": r.get("risk_score"),
                "notes": r.get("notes"),
                "patterns": r.get("patterns") or [],
                "ship_type": r.get("ship_type"),
            }.items() if v is not None}
    tail = _json.dumps({"names": names, "watch": watch},
                       separators=(",", ":")).encode()
    head = b"DSB1" + n.to_bytes(4, "little")
    return b"".join((head, mmsi.tobytes(), ts.tobytes(), lat.tobytes(),
                     lon.tobytes(), sog.tobytes(), cog.tobytes(),
                     heading.tobytes(), flags.tobytes(),
                     len(tail).to_bytes(4, "little"), tail))


# In the web/worker split only the WORKER runs the ingester, so a web pod's
# in-memory world_snapshot is permanently empty. Fall back to the same picture
# from the DB (latest position per ship, last 30 min), cached like /latest so
# pollers don't stampede the DISTINCT-ON query.
_WORLD_TTL = 30.0
_world_cache: tuple[float, bytes, bytes] | None = None  # (ts, raw, gz)
_world_lock = asyncio.Lock()


async def _build_world_from_db() -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(minutes=30)
    from ..db import SessionLocal
    async with SessionLocal() as session:
        result = await session.execute(
            select(LatestPosition.mmsi, LatestPosition.ts, LatestPosition.lat,
                   LatestPosition.lon, LatestPosition.sog, LatestPosition.cog,
                   LatestPosition.ship_name)
            .where(LatestPosition.ts >= since)
        )
        # serialize ONCE here (30k rows through FastAPI's encoder per request
        # cost ~2s of CPU); cache the finished bytes like /latest does. Trim
        # nulls + coordinate precision (5 dp ~ 1 m) - it's a big payload.
        rows = []
        for r in result:
            d = {"mmsi": r.mmsi, "ts": r.ts.isoformat(timespec="seconds"),
                 "lat": round(r.lat, 5), "lon": round(r.lon, 5)}
            if r.sog is not None:
                d["sog"] = round(r.sog, 1)
            if r.cog is not None:
                d["cog"] = round(r.cog, 1)
            if r.ship_name:
                d["ship_name"] = r.ship_name
            rows.append(d)
        return json.dumps(rows).encode()


async def _rebuild_world() -> tuple[bytes, bytes]:
    global _world_cache
    raw = await _build_world_from_db()
    gz = gzip.compress(raw, compresslevel=6)
    _world_cache = (time.monotonic(), raw, gz)
    return raw, gz


async def _refresh_world_bg() -> None:
    async with _world_lock:
        if _world_cache and time.monotonic() - _world_cache[0] < _WORLD_TTL:
            return
        try:
            await _rebuild_world()
        except Exception:
            pass  # keep serving the stale copy; next request retries


@router.get("/positions/world")
async def world_positions(request: Request):
    """Live snapshot of every terrestrially received ship worldwide - the
    map's ambient layer. Served from the ingester's memory when this process
    runs it (worker / dev); on the web pods from latest_positions via a
    stale-while-revalidate bytes cache, so no request waits on a rebuild."""
    from ..ingest.aisstream import get_world_snapshot
    snapshot = get_world_snapshot()
    if snapshot:
        return snapshot
    cached = _world_cache
    if cached:
        if time.monotonic() - cached[0] >= _WORLD_TTL and not _world_lock.locked():
            asyncio.get_running_loop().create_task(_refresh_world_bg())
        return _latest_response(request, cached[1], cached[2])
    async with _world_lock:
        if _world_cache:
            return _latest_response(request, _world_cache[1], _world_cache[2])
        raw, gz = await _rebuild_world()
        return _latest_response(request, raw, gz)


@router.get("/regions", response_model=list[RegionOut])
async def regions():
    return get_settings().ais_regions


# The clusters result is identical for every viewer and costs ~100ms of DB +
# clustering work; the map polls it every 60s. Cache the finished bytes so N
# viewers cause ONE rebuild per window, not N recomputes, and let a CDN serve
# the shared copy (same pattern as /positions/latest).
_CLUSTERS_TTL = 60.0
_clusters_cache: tuple[float, bytes, bytes] | None = None  # (ts, raw, gz)
_clusters_lock = asyncio.Lock()
_CLUSTERS_HEADERS = {"Cache-Control": "public, max-age=60", "Vary": "Accept-Encoding"}


@router.get("/clusters")
async def clusters(request: Request):
    """Interesting spots: fleet anchorages + behaviour hotspots. Served from a
    short-lived shared cache (identical for everyone), so heavy clustering runs
    once per minute rather than once per poll per viewer."""
    global _clusters_cache
    if _clusters_cache and time.monotonic() - _clusters_cache[0] < _CLUSTERS_TTL:
        return _cached_json(request, _clusters_cache[1], _clusters_cache[2], _CLUSTERS_HEADERS)
    async with _clusters_lock:
        if _clusters_cache and time.monotonic() - _clusters_cache[0] < _CLUSTERS_TTL:
            return _cached_json(request, _clusters_cache[1], _clusters_cache[2], _CLUSTERS_HEADERS)
        from ..db import SessionLocal
        async with SessionLocal() as session:
            data = await _build_clusters(session)
        raw = json.dumps(data).encode()
        gz = gzip.compress(raw, compresslevel=6)
        _clusters_cache = (time.monotonic(), raw, gz)
        return _cached_json(request, raw, gz, _CLUSTERS_HEADERS)


def _cached_json(request: Request, raw: bytes, gz: bytes, headers: dict) -> Response:
    if "gzip" in request.headers.get("accept-encoding", ""):
        return Response(gz, media_type="application/json",
                        headers={**headers, "Content-Encoding": "gzip"})
    return Response(raw, media_type="application/json", headers=headers)


async def _build_clusters(session: AsyncSession) -> list[dict]:
    """Groups of 3+ watchlist ships sitting anchored close together - the
    ship-to-ship staging signature. A huddle of sanctioned tankers holding
    position in one spot is where oil gets transferred and re-documented."""
    from ..geo import haversine_km

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    # Same 24h latest-position snapshot the map draws, so the counts here match
    # the dots the viewer can see in the area (the old 6h Position-scan silently
    # dropped ships that went quiet while anchored - shadow fleet behaviour).
    rows = (await session.execute(
        select(LatestPosition, Vessel.name.label("vessel_name"), Vessel.category)
        .join(Vessel, (Vessel.mmsi == LatestPosition.mmsi) & Vessel.active.is_(True))
        .where(LatestPosition.ts >= since)
    )).all()
    flagged = [{
        "mmsi": r.LatestPosition.mmsi, "lat": r.LatestPosition.lat,
        "lon": r.LatestPosition.lon, "sog": r.LatestPosition.sog,
        "vessel_name": r.vessel_name, "category": r.category,
    } for r in rows]
    # Cluster membership stays strict: anchored, genuinely list-flagged ships.
    # The 'other' category (behaviour-only + MoU-detention-only) is not a
    # sanctions/shadow-fleet listing and would inflate the anchorage counts -
    # but those ships still show up in the per-cluster "in the area" tally below.
    # NB: sog == 0.0 IS anchored - `sog or 99` treated a dead stop as unknown
    # and silently dropped most of every anchorage from the count.
    ships = [s for s in flagged
             if s["category"] != "other"
             and s["sog"] is not None and s["sog"] < 2.0]

    # Which of these are on an actual GOVERNMENT sanctions list (vs only on a
    # shadow-fleet watchlist like UANI/GUR) - so the panel can honestly say
    # "N shadow-fleet ships, M sanctioned" rather than calling them all sanctioned.
    gov_rules = ["risklist_ofac", "risklist_eu", "risklist_uk",
                 "risklist_canada", "risklist_switzerland", "risklist_un1718"]
    ship_mmsis = [s["mmsi"] for s in ships]
    sanctioned: set[int] = set()
    if ship_mmsis:
        for (m,) in await session.execute(
            select(RiskEvent.mmsi).where(
                RiskEvent.mmsi.in_(ship_mmsis), RiskEvent.rule.in_(gov_rules)
            ).distinct()
        ):
            sanctioned.add(m)

    # Transitive spatial clustering: a whole anchorage is ONE cluster, not
    # several fragments. Ships link if within 12 km of ANY cluster member, so
    # the Port Said or Singapore anchorage merges into a single group.
    unclustered = list(ships)
    groups = []
    while unclustered:
        group = [unclustered.pop()]
        changed = True
        while changed:
            changed = False
            rest = []
            for s in unclustered:
                if any(haversine_km(g["lat"], g["lon"], s["lat"], s["lon"]) <= 12.0 for g in group):
                    group.append(s)
                    changed = True
                else:
                    rest.append(s)
            unclustered = rest
        if len(group) >= 3:
            groups.append(group)

    # Recent behaviour alerts fired by any grouped ship (rendezvous, went dark,
    # draught change, ...) - the activity that makes a gathering interesting.
    # Static risklist_* memberships are excluded: being on a list isn't an event.
    member_mmsis = [s["mmsi"] for g in groups for s in g]
    events_by_mmsi: dict[int, list[str]] = {}
    if member_mmsis:
        for m, rule in await session.execute(
            select(RiskEvent.mmsi, RiskEvent.rule).where(
                RiskEvent.mmsi.in_(member_mmsis),
                RiskEvent.ts >= now - timedelta(hours=72),
                RiskEvent.rule.notlike("risklist_%"),
            )
        ):
            events_by_mmsi.setdefault(m, []).append(rule)

    def region_of(lat: float, lon: float):
        """The monitored region a point falls in - names the place and says
        whether anchoring is normal there (sts) or suspicious (transit)."""
        for r in get_settings().ais_regions:
            (lat0, lon0), (lat1, lon1) = r.bbox
            if lat0 <= lat <= lat1 and lon0 <= lon <= lon1:
                return r
        return None

    clusters = []
    for group in groups:
        clat = sum(s["lat"] for s in group) / len(group)
        clon = sum(s["lon"] for s in group) / len(group)
        member_set = {s["mmsi"] for s in group}
        # every other flagged ship (any category, moving or not) within the
        # cluster radius of a member: the honest "in the area" tally
        nearby = sum(
            1 for f in flagged if f["mmsi"] not in member_set and any(
                haversine_km(g["lat"], g["lon"], f["lat"], f["lon"]) <= 12.0 for g in group))
        alert_counts: dict[str, int] = {}
        for s in group:
            for rule in events_by_mmsi.get(s["mmsi"], []):
                label = PATTERN_LABELS.get(rule, rule)
                alert_counts[label] = alert_counts.get(label, 0) + 1
        region = region_of(clat, clon)
        clusters.append({
            "kind": "anchorage",
            "lat": round(clat, 3), "lon": round(clon, 3), "count": len(group),
            "nearby": nearby,
            "sanctioned": sum(1 for s in group if s["mmsi"] in sanctioned),
            "region": region.name if region else None,
            "region_kind": region.kind if region else None,
            "recent_alerts": sorted(
                ({"pattern": p, "count": n} for p, n in alert_counts.items()),
                key=lambda a: -a["count"]),
            "members": sorted(
                ({"mmsi": s["mmsi"], "name": s["vessel_name"], "category": s["category"]}
                 for s in group), key=lambda m: m["name"] or ""),
        })
    clusters.sort(key=lambda c: -c["count"])

    hotspots = await _activity_hotspots(session, now, region_of)
    return clusters + hotspots


def _event_coords(rule: str, detail: dict) -> tuple[float, float] | None:
    """Best (lat, lon) for a risk event from its detail blob - rules store the
    location under different keys."""
    for key in ("lat", "lon"):
        if key not in detail:
            break
    else:
        if isinstance(detail["lat"], (int, float)):
            return detail["lat"], detail["lon"]
    for key in ("to", "actual", "cluster_a"):  # [lat, lon] pairs
        v = detail.get(key)
        if isinstance(v, (list, tuple)) and len(v) == 2:
            return v[0], v[1]
    return None


async def _activity_hotspots(session, now, region_of) -> list[dict]:
    """The SECOND kind of interesting place: an AREA where a lot of weird
    behaviour happens - ships going dark, spoofing jumps, appearing mid-sea,
    draught changes - even when they are NOT anchored together. A concentration
    of anomalous EVENTS in one patch of sea is itself a signal that something
    operational is going on there (a laundering corridor, a dark-transfer zone).
    """
    import json as _json
    from collections import defaultdict

    since = now - timedelta(hours=72)
    # movement/behaviour anomalies only - list memberships are not a place
    rules = [r for r in PATTERN_LABELS if not r.startswith("risklist_")]
    rows = (await session.execute(
        select(RiskEvent.mmsi, RiskEvent.rule, RiskEvent.details)
        .where(RiskEvent.ts >= since, RiskEvent.rule.in_(rules))
    )).all()

    # Fixed ~0.5-degree grid, NOT transitive clustering - a hotspot is a bounded
    # ~50 km patch, not a chain that swallows the whole North Sea. Each cell
    # accumulates its distinct ships and per-pattern counts.
    CELL = 0.5
    cells: dict[tuple[int, int], dict] = defaultdict(
        lambda: {"ships": set(), "counts": defaultdict(int), "lat": 0.0, "lon": 0.0, "n": 0})
    for mmsi, rule, details in rows:
        try:
            d = _json.loads(details) if details else {}
        except (ValueError, TypeError):
            d = {}
        c = _event_coords(rule, d) if isinstance(d, dict) else None
        if not c or not (-90 <= c[0] <= 90 and -180 <= c[1] <= 180):
            continue
        cell = cells[(round(c[0] / CELL), round(c[1] / CELL))]
        cell["ships"].add(mmsi)
        cell["counts"][rule] += 1
        cell["lat"] += c[0]; cell["lon"] += c[1]; cell["n"] += 1

    hotspots: list[dict] = []
    for cell in cells.values():
        ships, counts, n = cell["ships"], cell["counts"], cell["n"]
        # a real hotspot needs DIVERSITY, not one repeated signal: >=3 different
        # ships, >=6 events, AND >=3 distinct pattern TYPES - so a coverage edge
        # where everyone merely "went dark" doesn't qualify; a patch with dark +
        # spoofing + mid-sea + draught changes (varied tradecraft) does.
        if len(ships) < 3 or n < 6 or len(counts) < 3:
            continue
        clat, clon = cell["lat"] / n, cell["lon"] / n
        region = region_of(clat, clon)
        # rank by how many DIFFERENT weird things happen here, then volume
        hotspots.append({
            "kind": "activity",
            "lat": round(clat, 3), "lon": round(clon, 3),
            "count": len(ships),          # distinct ships (map bubble size)
            "event_count": n,             # total anomalies in 72h
            "variety": len(counts),       # distinct pattern types
            "region": region.name if region else None,
            "region_kind": region.kind if region else None,
            "recent_alerts": sorted(
                ({"pattern": PATTERN_LABELS.get(r, r), "count": c}
                 for r, c in counts.items()), key=lambda a: -a["count"]),
        })
    hotspots.sort(key=lambda h: (-h["variety"], -h["event_count"]))
    return hotspots[:12]


@router.get("/position-checks/{check_id}/chip")
async def position_check_chip(check_id: int, kind: str = Query("radar", pattern="^(radar|optical)$"),
                              session: AsyncSession = Depends(get_session)):
    """The stored chip PNG behind a hull verdict - proxied from MinIO/Wasabi so
    the bucket stays private. kind=radar is the grayscale Sentinel-1 sigma0
    (the detector's evidence); kind=optical is the true-colour Sentinel-2
    companion when a cloud-free daylight pass existed."""
    from fastapi import HTTPException

    from ..services.chipstore import get_chip

    check = await session.get(PositionCheck, check_id)
    key = check.optical_chip_key if kind == "optical" else check.chip_key if check else None
    if not key:
        raise HTTPException(404, "no chip stored for this check")
    png = await get_chip(key)
    if png is None:
        raise HTTPException(404, "chip object unavailable")
    return Response(png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


@router.get("/positions/history/{mmsi}")
async def position_history(
    mmsi: int,
    start: datetime | None = None,
    end: datetime | None = None,
    # public + unauthenticated: bound the window so a single request can't fan
    # a year+ of cold-tier (S3/Wasabi) reads. Deep historical pulls belong behind
    # an authenticated/admin tool, not this open endpoint.
    days: float = Query(365, le=365),
):
    """Full track for one vessel, spanning the hot (Postgres) and cold (Parquet
    on S3/Wasabi) tiers transparently. Defaults to the last `days` days."""
    from ..services import cold

    now = datetime.now(timezone.utc)
    end = end or now
    start = start or (end - timedelta(days=days))
    points = await cold.query_track(mmsi, start, end)
    return {"mmsi": mmsi, "start": start.isoformat(), "end": end.isoformat(),
            "count": len(points), "points": points}


@router.get("/positions/cold/status")
async def cold_status():
    """What the cold tier holds: the archived months and their row counts."""
    from ..services import cold

    settings = get_settings()
    if not settings.cold_storage_enabled:
        return {"enabled": False, "months": []}
    # deliberately omit the bucket name - no need to disclose storage internals
    return {"enabled": True, "months": await cold.archived_months()}
