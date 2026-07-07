"""Gap detection: flags watchlist vessels that go dark.

Every run (5 min):
1. open a gap for vessels whose last position is older than the threshold
   and that were underway at the time (SOG above minimum - a moored ship
   switching AIS off is not suspicious);
2. close gaps as soon as a ship reappears and compute the displacement;
3. search Sentinel-1/2 acquisitions inside each gap's drift area.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ..config import get_settings
from ..db import SessionLocal
from ..geo import haversine_nm, project_position, search_polygon_wkt
from ..models import AisGap, Position, RiskEvent, SarMatch, Vessel
from ..services.copernicus import search_scenes
from .coverage import covered_since

logger = logging.getLogger(__name__)

SAR_RECHECK_HOURS = 6
MIN_DRIFT_SPEED_KNOTS = 5.0  # floor for the search area, even if the ship was moving slowly

# Drift-mismatch: dead-reckon where the ship SHOULD have reappeared from its
# last course/speed; a reappearance far off that prediction points to active
# manipulation rather than coverage loss. (Phase 2: refine with Copernicus
# Marine current data instead of straight-line dead reckoning.)
DRIFT_CHECK_MAX_HOURS = 48
DRIFT_MIN_SOG = 3.0
DRIFT_MISMATCH_SCORE = 30.0


def _drift_mismatch_event(gap: AisGap, last_cog: float | None) -> RiskEvent | None:
    if gap.gap_end is None or last_cog is None:
        return None
    hours = (gap.gap_end - gap.gap_start).total_seconds() / 3600
    if hours > DRIFT_CHECK_MAX_HOURS or (gap.last_sog or 0) < DRIFT_MIN_SOG:
        return None
    travel_nm = gap.last_sog * hours
    pred_lat, pred_lon = project_position(gap.last_lat, gap.last_lon, last_cog, travel_nm)
    mismatch_nm = haversine_nm(pred_lat, pred_lon, gap.first_lat_after, gap.first_lon_after)
    threshold_nm = max(30.0, 0.4 * travel_nm)
    if mismatch_nm <= threshold_nm:
        return None
    return RiskEvent(
        mmsi=gap.mmsi, rule="drift_mismatch", ts=datetime.now(timezone.utc),
        score=DRIFT_MISMATCH_SCORE,
        details=json.dumps({
            "gap_id": gap.id, "hours": round(hours, 1),
            "predicted": [round(pred_lat, 3), round(pred_lon, 3)],
            "actual": [gap.first_lat_after, gap.first_lon_after],
            "mismatch_nm": round(mismatch_nm, 1), "threshold_nm": round(threshold_nm, 1),
        }),
    )


async def run_gap_detection() -> None:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    threshold = timedelta(hours=settings.gap_threshold_hours)

    async with SessionLocal() as session:
        # only silences that started while we were provably listening count
        listening_since = await covered_since(session)

        vessels = (await session.execute(
            select(Vessel).where(Vessel.active.is_(True))
        )).scalars().all()

        open_gaps = {
            gap.mmsi: gap
            for gap in (await session.execute(
                select(AisGap).where(AisGap.status == "open")
            )).scalars()
        }

        for vessel in vessels:
            latest = (await session.execute(
                select(Position)
                .where(Position.mmsi == vessel.mmsi)
                .order_by(Position.ts.desc())
                .limit(1)
            )).scalars().first()
            if latest is None:
                continue  # never seen - not a gap, just no data yet

            gap = open_gaps.get(vessel.mmsi)
            if gap is not None:
                if latest.ts > gap.gap_start:
                    first_after = (await session.execute(
                        select(Position)
                        .where(Position.mmsi == vessel.mmsi, Position.ts > gap.gap_start)
                        .order_by(Position.ts.asc())
                        .limit(1)
                    )).scalars().first()
                    gap.gap_end = first_after.ts
                    gap.first_lat_after = first_after.lat
                    gap.first_lon_after = first_after.lon
                    gap.distance_nm = round(haversine_nm(
                        gap.last_lat, gap.last_lon, first_after.lat, first_after.lon), 1)
                    gap.status = "closed"
                    gap.sar_checked_at = None  # window is final now -> search once more
                    logger.info("Gap closed for MMSI %s: moved %.1f nm", vessel.mmsi, gap.distance_nm)
                    last_pos = (await session.execute(
                        select(Position.cog)
                        .where(Position.mmsi == vessel.mmsi, Position.ts <= gap.gap_start)
                        .order_by(Position.ts.desc()).limit(1)
                    )).first()
                    drift_event = _drift_mismatch_event(gap, last_pos.cog if last_pos else None)
                    if drift_event is not None:
                        session.add(drift_event)
                        logger.warning("Drift mismatch for MMSI %s: reappeared far off prediction", vessel.mmsi)
            elif (now - latest.ts > threshold
                  and (latest.sog or 0) >= settings.gap_min_sog_knots
                  and latest.ts >= listening_since):
                session.add(AisGap(
                    mmsi=vessel.mmsi,
                    gap_start=latest.ts,
                    last_lat=latest.lat,
                    last_lon=latest.lon,
                    last_sog=latest.sog,
                    status="open",
                ))
                logger.info("Gap opened for MMSI %s (last position %s)", vessel.mmsi, latest.ts)

        await session.commit()

    await _match_sar_scenes()


async def _match_sar_scenes() -> None:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    recheck_before = now - timedelta(hours=SAR_RECHECK_HOURS)

    async with SessionLocal() as session:
        gaps = (await session.execute(
            select(AisGap).where(
                (AisGap.sar_checked_at.is_(None)) | (
                    (AisGap.status == "open") & (AisGap.sar_checked_at < recheck_before)
                )
            )
        )).scalars().all()

        for gap in gaps:
            window_end = gap.gap_end or now
            hours = max((window_end - gap.gap_start).total_seconds() / 3600, 1)
            speed = max(gap.last_sog or 0, MIN_DRIFT_SPEED_KNOTS)
            radius = min(hours * speed, settings.drift_max_nm)
            polygon = search_polygon_wkt(gap.last_lat, gap.last_lon, radius)

            try:
                scenes = await search_scenes(polygon, gap.gap_start, window_end,
                                             gap.last_lat, gap.last_lon)
            except Exception:
                logger.exception("SAR matching failed for gap %d", gap.id)
                continue

            existing = {
                row[0] for row in await session.execute(
                    select(SarMatch.product_id).where(SarMatch.gap_id == gap.id)
                )
            }
            new = [s for s in scenes if s["product_id"] not in existing]
            for scene in new:
                session.add(SarMatch(gap_id=gap.id, **scene))
            gap.sar_checked_at = now
            if new:
                logger.info("Gap %d: %d new satellite scenes found", gap.id, len(new))

        await session.commit()
