import json
import asyncio
import gzip
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session
from ..models import LatestPosition, Position, RiskEvent, Vessel, VesselRegistry
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


async def _rebuild_latest(since_hours: float) -> tuple[bytes, bytes]:
    from ..db import SessionLocal
    async with SessionLocal() as session:
        raw = await _build_latest(session, since_hours)
    gz = gzip.compress(raw, compresslevel=6)  # compressed once per window
    _latest_cache[since_hours] = (time.monotonic(), raw, gz)
    return raw, gz


async def _refresh_latest_bg(since_hours: float) -> None:
    async with _latest_lock:
        cached = _latest_cache.get(since_hours)
        if cached and time.monotonic() - cached[0] < _LATEST_TTL:
            return  # someone else already refreshed
        try:
            await _rebuild_latest(since_hours)
        except Exception:
            pass  # keep serving the stale copy; next request retries


@router.get("/positions/latest", response_model=list[LatestPositionOut])
async def latest_positions(request: Request, since_hours: float = Query(24, le=24 * 7)):
    """Most recent position per vessel, incl. watchlist info. Served from a
    short-lived shared cache, stale-while-revalidate: an expired cache is
    served immediately while one background task rebuilds it - no visitor
    ever waits on the rebuild."""
    cached = _latest_cache.get(since_hours)
    if cached:
        if time.monotonic() - cached[0] >= _LATEST_TTL and not _latest_lock.locked():
            asyncio.get_running_loop().create_task(_refresh_latest_bg(since_hours))
        return _latest_response(request, cached[1], cached[2])
    async with _latest_lock:
        cached = _latest_cache.get(since_hours)  # built while we waited?
        if cached:
            return _latest_response(request, cached[1], cached[2])
        raw, gz = await _rebuild_latest(since_hours)
        return _latest_response(request, raw, gz)


async def _build_latest(session: AsyncSession, since_hours: float) -> bytes:
    """Build the current picture from latest_positions (one row per ship,
    maintained by the ingester) - NOT by DISTINCT-ON scanning the position
    history, which grows by millions of rows per day and took >10s."""
    since = datetime.now(timezone.utc) - timedelta(hours=since_hours)
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
    rows = []
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
    # exclude_none: most of the 50k+ rows are ambient traffic where category /
    # vessel_name / risk_score / notes etc. are null - dropping the null keys
    # roughly halves the payload (the frontend treats undefined as null)
    # exclude_defaults also drops patterns=[] on the ~50k ambient rows
    return _latest_adapter.dump_json(
        [LatestPositionOut(**r) for r in rows],
        exclude_none=True, exclude_defaults=True)


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


@router.get("/clusters")
async def clusters(session: AsyncSession = Depends(get_session)):
    """Groups of 3+ watchlist ships sitting anchored close together - the
    ship-to-ship staging signature. A huddle of sanctioned tankers holding
    position in one spot is where oil gets transferred and re-documented."""
    from ..geo import haversine_km

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=6)
    # Only genuinely list-flagged ships count as "shadow fleet" here: exclude the
    # 'other' category (behaviour-only + MoU-detention-only), which is not a
    # sanctions/shadow-fleet listing and would otherwise inflate the anchorage
    # counts with ordinary flagged traffic. (We also no longer tag a 'watchlist'
    # position source - every ship is recorded region/world - so scope by MMSI.)
    watch_mmsis = select(Vessel.mmsi).where(
        Vessel.active.is_(True), Vessel.category != "other"
    ).scalar_subquery()
    latest = (
        select(Position).where(Position.ts >= since, Position.mmsi.in_(watch_mmsis))
        .distinct(Position.mmsi).order_by(Position.mmsi, Position.ts.desc()).subquery()
    )
    rows = (await session.execute(
        select(latest, Vessel.name.label("vessel_name"), Vessel.category)
        .join(Vessel, (Vessel.mmsi == latest.c.mmsi) & Vessel.active.is_(True))
    )).all()
    # anchored ships only
    ships = [dict(r._mapping) for r in rows if (r._mapping["sog"] or 99) < 2.0]

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
    clusters = []
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
            clat = sum(s["lat"] for s in group) / len(group)
            clon = sum(s["lon"] for s in group) / len(group)
            clusters.append({
                "lat": round(clat, 3), "lon": round(clon, 3), "count": len(group),
                "sanctioned": sum(1 for s in group if s["mmsi"] in sanctioned),
                "members": sorted(
                    ({"mmsi": s["mmsi"], "name": s["vessel_name"], "category": s["category"]}
                     for s in group), key=lambda m: m["name"] or ""),
            })
    clusters.sort(key=lambda c: -c["count"])
    return clusters


@router.get("/positions/history/{mmsi}")
async def position_history(
    mmsi: int,
    start: datetime | None = None,
    end: datetime | None = None,
    # public + unauthenticated: bound the window so a single request can't fan
    # a year+ of cold-tier (S3/R2) reads. Deep historical pulls belong behind
    # an authenticated/admin tool, not this open endpoint.
    days: float = Query(365, le=365),
):
    """Full track for one vessel, spanning the hot (Postgres) and cold (Parquet
    on S3/R2) tiers transparently. Defaults to the last `days` days."""
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
