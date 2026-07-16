"""Follow digests: users who follow a ship get an email when it keeps
misbehaving - new risk events, or a satellite check that could NOT find it
where it claimed to be. Batched per user with a cooldown so an active ship
doesn't become spam; the digest links to the ship's dossier for the full
evidence trail."""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ..config import get_settings
from ..db import SessionLocal
from ..mail import send_follow_digest
from ..models import PositionCheck, RiskEvent, User, UserVessel, VesselRegistry
from ..api.user_events import _severity

logger = logging.getLogger(__name__)

COOLDOWN_HOURS = 3     # at most one digest per user per this window
MAX_WINDOW_HOURS = 24  # never dig further back than this on first send


async def run_follow_digests() -> None:
    from ..api.positions import PATTERN_LABELS

    now = datetime.now(timezone.utc)
    base = get_settings().frontend_base_url.rstrip("/")
    async with SessionLocal() as session:
        follows = (await session.execute(
            select(UserVessel.user_id, UserVessel.mmsi))).all()
        if not follows:
            return
        by_user: dict[int, set[int]] = {}
        for uid, mmsi in follows:
            by_user.setdefault(uid, set()).add(mmsi)

        users = {u.id: u for u in (await session.execute(
            select(User).where(User.id.in_(by_user), User.is_active.is_(True))
        )).scalars().unique()}

        sent = 0
        for uid, mmsis in by_user.items():
            user = users.get(uid)
            if user is None:
                continue
            if user.last_digest_at and now - user.last_digest_at < timedelta(hours=COOLDOWN_HOURS):
                continue
            since = max(user.last_digest_at or (now - timedelta(hours=MAX_WINDOW_HOURS)),
                        now - timedelta(hours=MAX_WINDOW_HOURS))

            events = (await session.execute(
                select(RiskEvent).where(
                    RiskEvent.mmsi.in_(mmsis), RiskEvent.ts > since)
                .order_by(RiskEvent.ts.desc()).limit(50))).scalars().all()
            misses = (await session.execute(
                select(PositionCheck).where(
                    PositionCheck.mmsi.in_(mmsis),
                    PositionCheck.analyzed_at > since,
                    PositionCheck.hull_detected.is_(False))
                .limit(20))).scalars().all()
            if not events and not misses:
                continue

            names = {r.mmsi: r.name for r in (await session.execute(
                select(VesselRegistry).where(VesselRegistry.mmsi.in_(
                    {e.mmsi for e in events} | {m.mmsi for m in misses})))).scalars()}

            lines = []
            for m in misses:
                nm = names.get(m.mmsi) or m.mmsi
                lines.append(
                    f"[satellite] {nm}: NOT found where it claimed to be on "
                    f"{m.acquired_at:%d/%m %H:%M} UTC - {base}/ship/{m.mmsi}/details")
            for e in events:
                nm = names.get(e.mmsi) or e.mmsi
                label = PATTERN_LABELS.get(e.rule, e.rule)
                lines.append(
                    f"[{_severity(e.rule, e.score)}] {nm}: {label} "
                    f"({e.ts:%d/%m %H:%M} UTC) - {base}/ship/{e.mmsi}/details")

            ships = len({*(e.mmsi for e in events), *(m.mmsi for m in misses)})
            body = (
                f"New activity on {ships} ship(s) you follow on Dark Ships:\n\n"
                + "\n".join(lines[:40])
                + "\n\nAutomated detections are an investigative aid, not a "
                  "legal finding. Manage your watchlist: " + base + "/monitor"
            )
            try:
                await send_follow_digest(
                    user.email,
                    f"Dark Ships: new activity on {ships} followed ship(s)",
                    body)
                user.last_digest_at = now
                sent += 1
            except Exception:
                logger.exception("Follow digest to %s failed", user.email)
        await session.commit()
        if sent:
            logger.info("Follow digests: %d sent", sent)
