"""Claimed-position verification against satellite imagery.

For every active watchlist vessel: search the (free, already-archived)
Sentinel-1/2 catalogue for scenes that captured the vessel's CLAIMED AIS
position. We do NOT wait for a fresh overpass - we mine the existing archive
back over LOOKBACK_DAYS and match each scene against where the ship actually
was at that scene's acquisition time.

The time-match tolerance is speed-aware: a MOVING ship must have an AIS ping
within minutes of the capture (it could be far away otherwise), but an
ANCHORED ship (SOG ~0) that our AIS history shows parked in one spot matches
any scene taken during that stay - so the EOPL tankers sitting still for days
match archived scenes of the anchorage immediately.

If the ship says it was there, a hull must be visible there; the Copernicus
Browser link makes it a one-click human check. (Automating the hull detection
itself, and finding DARK ships with no AIS at all, is the phase-2 SAR-detection
step - see Global Fishing Watch SAR presence.)
"""

import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

from ..db import SessionLocal
from ..geo import haversine_nm, search_polygon_wkt
from ..models import Position, PositionCheck, Vessel
from ..services.copernicus import search_scenes

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 14           # how far into the Sentinel archive to mine
MAX_DELTA_MOVING_MIN = 45    # a moving ship must be pinged within this of capture
MAX_DELTA_ANCHORED_MIN = 12 * 60  # an anchored ship didn't move, so widen a lot
ANCHORED_SOG = 1.5
MAX_SEARCH_RADIUS_NM = 120
# behaviour-only ships earn satellite verification at this risk score;
# sanctions-listed ships always get it. Kept as a light floor (not 0) so a
# single stray ping can't queue a check, but low enough that every ship with
# real accumulated suspicion is verified - the pipeline analyses highest-risk
# first, so a lower bar delays weak cases rather than starving strong ones.
MIN_BEHAVIOUR_SCORE_FOR_SAR = 50.0

_RING_RE = re.compile(r"\(\(([^()]+)\)\)")


def _point_in_footprint(lat: float, lon: float, footprint_wkt: str | None) -> bool:
    """Ray-cast against the first ring of the footprint polygon. Falls back to
    True when the footprint can't be parsed (we'd rather over-report scenes
    than silently drop a verification opportunity)."""
    if not footprint_wkt:
        return True
    match = _RING_RE.search(footprint_wkt)
    if not match:
        return True
    try:
        ring = [(float(x), float(y)) for x, y in
                (pair.strip().split()[:2] for pair in match.group(1).split(","))]
    except (ValueError, IndexError):
        return True
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-12) + xi:
            inside = not inside
        j = i
    return inside


async def run_position_checks() -> None:
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=LOOKBACK_DAYS)

    # Co-located ships (anchorage clusters) would otherwise each fire an
    # near-identical Copernicus query. Cache scene results per ~0.2-degree grid
    # cell so the 20 ships at one anchorage share ONE pair of queries.
    scene_cache: dict[tuple[int, int], list] = {}

    async with SessionLocal() as session:
        # Satellite verification is budgeted (the SAR job analyzes ~600 checks
        # a day): sanctions-listed ships always earn it; behaviour-only
        # ('other') ships must accumulate real suspicion first. Highest risk
        # gets mined first so the worst offenders are never starved by volume.
        vessels = (await session.execute(
            select(Vessel).where(
                Vessel.active.is_(True),
                or_(Vessel.category != "other",
                    Vessel.risk_score >= MIN_BEHAVIOUR_SCORE_FOR_SAR))
            .order_by(Vessel.risk_score.desc())
        )).scalars().all()

        for vessel in vessels:
            track = (await session.execute(
                select(Position)
                .where(Position.mmsi == vessel.mmsi, Position.ts >= since)
                .order_by(Position.ts)
            )).scalars().all()
            if not track:
                continue

            lat_c = sum(p.lat for p in track) / len(track)
            lon_c = sum(p.lon for p in track) / len(track)
            spread_deg = max(
                max(abs(p.lat - lat_c) for p in track),
                max(abs(p.lon - lon_c) for p in track),
            )
            radius_nm = min(max(spread_deg * 60 + 15, 20), MAX_SEARCH_RADIUS_NM)

            # reuse a nearby ship's query if one already ran this cell this pass
            cell = (round(lat_c * 5), round(lon_c * 5))  # ~0.2-degree grid
            if cell in scene_cache:
                scenes = scene_cache[cell]
            else:
                # widen the shared query a little so cell-mates are covered; the
                # per-scene footprint check below keeps matching exact
                polygon = search_polygon_wkt(lat_c, lon_c, max(radius_nm, 30))
                try:
                    scenes = await search_scenes(polygon, since, now, lat_c, lon_c)
                except Exception:
                    logger.exception("Position-check scene search failed for MMSI %s", vessel.mmsi)
                    scene_cache[cell] = []
                    continue
                scene_cache[cell] = scenes
            if not scenes:
                continue

            # Is the ship CURRENTLY anchored? (recent pings slow + tightly
            # clustered.) An anchored ship sits put for days/weeks, so any
            # archived scene over its current anchorage captured it - we don't
            # need an AIS ping at the exact scene time.
            recent = [p for p in track if (now - p.ts) <= timedelta(hours=6)]
            anchor_pos = None
            if recent:
                rlat = sum(p.lat for p in recent) / len(recent)
                rlon = sum(p.lon for p in recent) / len(recent)
                slow = max((p.sog or 99) for p in recent) < ANCHORED_SOG
                tight = all(haversine_nm(rlat, rlon, p.lat, p.lon) < 2 for p in recent)
                if slow and tight:
                    anchor_pos = (rlat, rlon, max(p.ts for p in recent))

            existing = {
                row[0] for row in await session.execute(
                    select(PositionCheck.product_id).where(PositionCheck.mmsi == vessel.mmsi)
                )
            }
            added = 0
            for scene in scenes:
                # Radar only: the auto-verify pipeline (jobs/sar_detector.py)
                # can only judge Sentinel-1 chips; optical scenes would sit
                # "pending" forever, so we don't store them as checks. (Gap
                # scenes keep both sources - they're for humans/quicklooks.)
                if scene["source"] != "sentinel-1":
                    continue
                if scene["product_id"] in existing:
                    continue
                if anchor_pos is not None:
                    # verify the scene covers the current anchorage; time gap is
                    # informational (the ship is parked there now)
                    lat_p, lon_p, last_ts = anchor_pos
                    delta_min = abs((last_ts - scene["acquired_at"]).total_seconds()) / 60
                    if not _point_in_footprint(lat_p, lon_p, scene["footprint_wkt"]):
                        continue
                    claim_lat, claim_lon, claim_ts = lat_p, lon_p, last_ts
                else:
                    # moving ship: need an AIS ping close in time to the capture
                    nearest = min(track, key=lambda p: abs((p.ts - scene["acquired_at"]).total_seconds()))
                    delta_min = abs((nearest.ts - scene["acquired_at"]).total_seconds()) / 60
                    if delta_min > MAX_DELTA_MOVING_MIN:
                        continue
                    if (nearest.sog or 0) * (delta_min / 60) > 5:  # drifted too far
                        continue
                    if not _point_in_footprint(nearest.lat, nearest.lon, scene["footprint_wkt"]):
                        continue
                    claim_lat, claim_lon, claim_ts = nearest.lat, nearest.lon, nearest.ts
                session.add(PositionCheck(
                    mmsi=vessel.mmsi,
                    claimed_ts=claim_ts, claimed_lat=claim_lat, claimed_lon=claim_lon,
                    source=scene["source"], product_id=scene["product_id"],
                    product_name=scene["product_name"], acquired_at=scene["acquired_at"],
                    delta_minutes=round(delta_min, 1),
                    quicklook_url=scene["quicklook_url"], browser_url=scene["browser_url"],
                ))
                existing.add(scene["product_id"])
                added += 1
            if added:
                await session.commit()  # persist per-vessel so a slow run isn't all-or-nothing
                logger.info("Position checks: %d archived scene(s) captured MMSI %s at its claimed spot",
                            added, vessel.mmsi)
        await session.commit()
