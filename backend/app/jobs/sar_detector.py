"""Automated SAR ship detection - phase 2 of the position checker.

run_position_checks stores Sentinel-1 scenes that captured each watchlist
vessel's CLAIMED position. This job closes the loop: for every unanalyzed
sentinel-1 check it fetches a small sigma0 chip around the claim (Sentinel
Hub Process API), runs the CFAR-style detector, and records whether a radar
target is actually present where the ship said it was. The rendered chip is
stored on MinIO/R2 so the UI (and a human) can see exactly what the
satellite measured; the Copernicus Browser link remains the ground truth.

Skips cleanly when CDSE_SH_CLIENT_ID/SECRET are not configured."""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ..config import get_settings
from ..db import SessionLocal
from ..models import PositionCheck
from ..services.chipstore import put_chip
from ..services.sardetect import (detect_ships, render_chip_png,
                                  target_is_persistent)
from ..services.sentinelhub import fetch_s1_chip

logger = logging.getLogger(__name__)

# Per run: keeps a 6-hourly job well inside the CDSE free-tier processing
# quota (a 300px float32 chip is <1 PU of the 30k/month) even with a large
# backlog; the backlog drains across runs.
BATCH_SIZE = 150
# Reference pass for persistent-target suppression: far enough back that a
# normal port call / short anchorage doesn't span both passes, close enough
# that a S1 acquisition of the spot exists.
REF_WINDOW_START_DAYS = 45
REF_WINDOW_END_DAYS = 15


async def run_sar_detection() -> None:
    if not get_settings().sar_detection_enabled:
        logger.info("SAR detection skipped: CDSE_SH_CLIENT_ID/SECRET not set")
        return

    async with SessionLocal() as session:
        checks = (await session.execute(
            select(PositionCheck)
            .where(PositionCheck.source == "sentinel-1",
                   PositionCheck.analyzed_at.is_(None))
            .order_by(PositionCheck.acquired_at.desc())
            .limit(BATCH_SIZE)
        )).scalars().all()
        if not checks:
            return

        analyzed = detected = deleted = 0
        for check in checks:
            fetched = await fetch_s1_chip(
                check.claimed_lat, check.claimed_lon, check.acquired_at)
            if fetched is None:
                # fetch/auth error: leave analyzed_at NULL so the next run
                # retries instead of burying the check as "no result"
                continue
            chip, m_per_px = fetched
            result = detect_ships(chip, m_per_px)

            if not result.valid:
                # auto-verify workflow: an unjudgeable chip (AOI mostly outside
                # the swath) verifies nothing - drop the check instead of
                # accumulating rows a human would have to triage
                await session.delete(check)
                await session.commit()
                deleted += 1
                continue

            check.analyzed_at = datetime.now(timezone.utc)
            check.hull_detected = result.hull_detected
            check.target_count = len(result.detections)
            check.nearest_offset_m = result.nearest_offset_m

            if result.hull_detected:
                # cross-check against a pass weeks earlier: a "hull" that was
                # already there is likely a fixed structure (turbine, platform)
                # or a very long-anchored ship - flagged, not guessed away
                ref = await fetch_s1_chip(
                    check.claimed_lat, check.claimed_lon,
                    t_from=check.acquired_at - timedelta(days=REF_WINDOW_START_DAYS),
                    t_to=check.acquired_at - timedelta(days=REF_WINDOW_END_DAYS))
                if ref is not None:
                    check.persistent_target = target_is_persistent(
                        result, detect_ships(ref[0], ref[1]), m_per_px)

            check.chip_key = await put_chip(check.id, render_chip_png(chip))
            analyzed += 1
            detected += 1 if check.hull_detected else 0
            await session.commit()  # per-check: a crash mid-batch loses nothing

        logger.info(
            "SAR detection: %d analyzed (%d hulls confirmed), %d unjudgeable deleted",
            analyzed, detected, deleted)
