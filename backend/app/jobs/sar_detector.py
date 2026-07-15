"""Automated SAR ship detection - phase 2 of the position checker.

run_position_checks stores Sentinel-1 scenes that captured each watchlist
vessel's CLAIMED position. This job closes the loop: for every unanalyzed
sentinel-1 check it fetches a small sigma0 chip around the claim (Sentinel
Hub Process API), runs the CFAR-style detector, and records whether a radar
target is actually present where the ship said it was. The rendered chip is
stored on MinIO locally / Wasabi in prod so the UI (and a human) can see exactly what the
satellite measured; the Copernicus Browser link remains the ground truth.

Skips cleanly when CDSE_SH_CLIENT_ID/SECRET are not configured."""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from ..config import get_settings
from ..db import SessionLocal
from ..models import PositionCheck, VesselRegistry
from ..services.chipstore import delete_chip, list_chip_keys, put_chip
from ..services.sardetect import (MATCH_RADIUS_M, detect_ships,
                                  render_chip_png, size_plausible,
                                  target_is_persistent)
from ..services.shipdetect import detect_ships_ml, model_available
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


def _detect(chip, m_per_px):
    """Stage 1: the YOLO ship detector when the model ships with the image,
    else the classical CFAR thresholder (same result shape)."""
    return detect_ships_ml(chip, m_per_px) or detect_ships(chip, m_per_px)


async def analyze_check(session, check: PositionCheck) -> str:
    """Run the full pipeline for one stored check: fetch chip -> detect ships
    (stage 1) -> size gate against the vessel's AIS dimensions (stage 2) ->
    persistence cross-check + evidence chip (stage 3). Commits the outcome.
    Returns 'analyzed', 'deleted' (unjudgeable chip) or 'skipped' (fetch
    error; stays pending for the next run)."""
    fetched = await fetch_s1_chip(
        check.claimed_lat, check.claimed_lon, check.acquired_at)
    if fetched is None:
        return "skipped"
    chip, m_per_px = fetched
    result = _detect(chip, m_per_px)

    if not result.valid:
        # auto-verify workflow: an unjudgeable chip (AOI mostly outside the
        # swath, or land clutter at the claim for the CFAR path) verifies
        # nothing - drop the check instead of accumulating triage rows
        if check.chip_key:  # re-analysis of a previously chipped check
            await delete_chip(check.chip_key)
        await session.delete(check)
        await session.commit()
        return "deleted"

    # Stage 2 - can the detected target BE this ship? A detection near the
    # claim only confirms the hull when its measured size is plausible for
    # the vessel's AIS dimensions (when we know them).
    at_claim = [d for d in result.detections if d.offset_m <= MATCH_RADIUS_M]
    reg = await session.get(VesselRegistry, check.mmsi)
    ship_len = reg.length_m if reg and reg.length_m else None
    chosen = None
    if at_claim:
        if ship_len:
            plausible = [d for d in at_claim
                         if size_plausible(d.length_m, ship_len)]
            chosen = plausible[0] if plausible else None
            check.size_match = bool(plausible)
        else:
            chosen = at_claim[0]
            check.size_match = None  # no AIS dimensions to compare

    check.analyzed_at = datetime.now(timezone.utc)
    check.hull_detected = chosen is not None
    check.target_count = len(result.detections)
    reported = chosen or (at_claim[0] if at_claim else None)
    check.nearest_offset_m = (reported.offset_m if reported
                              else result.nearest_offset_m)
    check.target_length_m = reported.length_m if reported else None

    if check.hull_detected:
        # cross-check against a pass weeks earlier: a "hull" that was already
        # there is likely a fixed structure (turbine, platform) or a very
        # long-anchored ship - flagged, not guessed away
        ref = await fetch_s1_chip(
            check.claimed_lat, check.claimed_lon,
            t_from=check.acquired_at - timedelta(days=REF_WINDOW_START_DAYS),
            t_to=check.acquired_at - timedelta(days=REF_WINDOW_END_DAYS))
        if ref is not None:
            check.persistent_target = target_is_persistent(
                result, _detect(ref[0], ref[1]), m_per_px)

    check.chip_key = await put_chip(check.id, render_chip_png(chip))
    await session.commit()  # per-check: a crash mid-batch loses nothing
    return "analyzed"


async def run_sar_detection() -> None:
    if not get_settings().sar_detection_enabled:
        logger.info("SAR detection skipped: CDSE_SH_CLIENT_ID/SECRET not set")
        return
    logger.info("SAR detection using %s detector",
                "ML (YOLO/SSDD)" if model_available() else "CFAR fallback")

    async with SessionLocal() as session:
        if get_settings().cold_storage_enabled:
            # self-heal: a verdict without its evidence chip means the upload
            # failed at analysis time (e.g. bad storage creds) - requeue those
            # checks so they re-analyze and the image gets stored
            requeued = await session.execute(
                update(PositionCheck)
                .where(PositionCheck.source == "sentinel-1",
                       PositionCheck.analyzed_at.isnot(None),
                       PositionCheck.chip_key.is_(None))
                .values(analyzed_at=None, hull_detected=None, target_count=None,
                        nearest_offset_m=None, persistent_target=None,
                        target_length_m=None, size_match=None))
            if requeued.rowcount:
                await session.commit()
                logger.info("SAR detection: requeued %d verdicts missing their "
                            "evidence chip", requeued.rowcount)

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
            outcome = await analyze_check(session, check)
            analyzed += outcome == "analyzed"
            deleted += outcome == "deleted"
            detected += outcome == "analyzed" and bool(check.hull_detected)

        logger.info(
            "SAR detection: %d analyzed (%d hulls confirmed), %d unjudgeable deleted",
            analyzed, detected, deleted)

        await cleanup_orphan_chips(session)


async def cleanup_orphan_chips(session) -> None:
    """Delete stored chip objects that no position_check references anymore
    (deleted checks, deduped duplicate rows). Chips for no-target verdicts are
    NOT orphans - the empty sea is the evidence for those checks."""
    stored = await list_chip_keys()
    if not stored:
        return
    referenced = set((await session.execute(
        select(PositionCheck.chip_key).where(PositionCheck.chip_key.isnot(None))
    )).scalars())
    orphans = [k for k in stored if k not in referenced]
    for key in orphans:
        await delete_chip(key)
    if orphans:
        logger.info("Chip cleanup: %d orphaned objects removed", len(orphans))
