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
from datetime import datetime, timezone

from sqlalchemy import select

from ..config import get_settings
from ..db import SessionLocal
from ..models import PositionCheck
from ..services.chipstore import put_chip
from ..services.sardetect import detect_ships, render_chip_png
from ..services.sentinelhub import fetch_s1_chip

logger = logging.getLogger(__name__)

# Per run: keeps a 6-hourly job well inside the CDSE free-tier processing
# quota even with a large backlog; the backlog drains across runs.
BATCH_SIZE = 40


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

        analyzed = detected = 0
        for check in checks:
            fetched = await fetch_s1_chip(
                check.claimed_lat, check.claimed_lon, check.acquired_at)
            if fetched is None:
                # fetch/auth error: leave analyzed_at NULL so the next run
                # retries instead of burying the check as "no result"
                continue
            chip, m_per_px = fetched
            result = detect_ships(chip, m_per_px)

            check.analyzed_at = datetime.now(timezone.utc)
            if result.valid:
                check.hull_detected = result.hull_detected
                check.target_count = len(result.detections)
                check.nearest_offset_m = result.nearest_offset_m
            # else: analyzed but unjudgeable (chip mostly outside the swath) -
            # hull_detected stays NULL, which the UI shows as "no verdict"

            check.chip_key = await put_chip(check.id, render_chip_png(chip))
            analyzed += 1
            detected += 1 if check.hull_detected else 0
            await session.commit()  # per-check: a crash mid-batch loses nothing

        logger.info("SAR detection: %d/%d checks analyzed, %d hulls confirmed",
                    analyzed, len(checks), detected)
