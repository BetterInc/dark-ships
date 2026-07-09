import io
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session
from ..jobs.behavior import SCORE_WINDOW_DAYS
from ..models import Position, RiskEvent, RiskListEntry, Vessel, VesselRegistry
from ..services.risklists import import_all

router = APIRouter(prefix="/api", tags=["suggestions"])


class RiskEventOut(BaseModel):
    rule: str
    ts: datetime
    score: float
    details: dict


class SuggestionOut(BaseModel):
    mmsi: int
    imo: str | None
    name: str | None
    score: float
    on_watchlist: bool
    last_seen: datetime | None
    lat: float | None = None  # live position from the world snapshot, for the map
    lon: float | None = None
    events: list[RiskEventOut]


@router.get("/suggestions", response_model=list[SuggestionOut])
async def list_suggestions(session: AsyncSession = Depends(get_session)):
    """Vessels flagged by the behaviour engine, highest risk first.
    Shows everything at >= half the auto-add threshold so you see what's brewing."""
    settings = get_settings()
    since = datetime.now(timezone.utc) - timedelta(days=SCORE_WINDOW_DAYS)

    # Only suggest vessels WE have actually tracked (have a real position for) -
    # otherwise the feed fills with phantom GFW-only ships we can't show on the
    # map or locate. GFW processes far more AIS than we receive, so ~83% of its
    # flagged vessels never appear in our own feed; those are not actionable
    # leads for a viewer, so they are excluded here.
    tracked = exists(select(Position.id).where(
        Position.mmsi == RiskEvent.mmsi, Position.ts >= since))
    scores = (await session.execute(
        select(RiskEvent.mmsi, func.sum(RiskEvent.score))
        .where(RiskEvent.ts >= since, tracked)
        .group_by(RiskEvent.mmsi)
        .having(func.sum(RiskEvent.score) >= settings.suggestion_min_score / 2)
        .order_by(func.sum(RiskEvent.score).desc())
        .limit(100)
    )).all()
    if not scores:
        return []

    mmsis = [m for m, _ in scores]
    registry = {
        r.mmsi: r for r in (await session.execute(
            select(VesselRegistry).where(VesselRegistry.mmsi.in_(mmsis)))).scalars()
    }
    watchlisted = {
        row[0] for row in await session.execute(
            select(Vessel.mmsi).where(Vessel.mmsi.in_(mmsis), Vessel.active.is_(True)))
    }
    events_by_mmsi: dict[int, list[RiskEvent]] = {}
    for e in (await session.execute(
        select(RiskEvent).where(RiskEvent.mmsi.in_(mmsis), RiskEvent.ts >= since)
        .order_by(RiskEvent.ts.desc())
    )).scalars():
        events_by_mmsi.setdefault(e.mmsi, []).append(e)

    from ..ingest.aisstream import world_snapshot

    # The in-memory snapshot only exists in the process that runs the ingester
    # (worker / local all-in-one). On the web pods it is empty, which used to
    # null every lat/lon here - so the map never drew the suspect rings in
    # production. Fall back to the latest stored position for these <=100 ships.
    latest_pos: dict[int, tuple[float, float]] = {}
    missing = [m for m in mmsis if m not in world_snapshot]
    if missing:
        for m, lat, lon in await session.execute(
            select(Position.mmsi, Position.lat, Position.lon)
            .where(Position.mmsi.in_(missing), Position.ts >= since)
            .distinct(Position.mmsi)
            .order_by(Position.mmsi, Position.ts.desc())
        ):
            latest_pos[m] = (lat, lon)

    out = []
    for mmsi, score in scores:
        reg = registry.get(mmsi)
        live = world_snapshot.get(mmsi)
        pos = (live["lat"], live["lon"]) if live else latest_pos.get(mmsi)
        out.append(SuggestionOut(
            mmsi=mmsi,
            imo=reg.imo if reg else None,
            name=reg.name if reg else None,
            score=float(score),
            on_watchlist=mmsi in watchlisted,
            last_seen=reg.last_seen if reg else None,
            lat=pos[0] if pos else None,
            lon=pos[1] if pos else None,
            events=[RiskEventOut(rule=e.rule, ts=e.ts, score=e.score,
                                 details=json.loads(e.details or "{}"))
                    for e in events_by_mmsi.get(mmsi, [])[:10]],
        ))
    return out


@router.post("/suggestions/{mmsi}/add", status_code=201)
async def accept_suggestion(mmsi: int, session: AsyncSession = Depends(get_session)):
    """Manually accept a suggestion - added as pinned (never auto-evicted)."""
    existing = await session.scalar(select(Vessel).where(Vessel.mmsi == mmsi))
    if existing:
        existing.active = True
        existing.pinned = True
    else:
        reg = await session.get(VesselRegistry, mmsi)
        session.add(Vessel(
            mmsi=mmsi, imo=(reg.imo if reg else None), name=(reg.name if reg else None),
            category="other", notes="Accepted from suggestions",
            pinned=True, auto_added=False,
        ))
    await session.commit()
    return {"status": "added"}


# source -> (display name, official total published, licence, auto-updates?, note)
# Official = authoritative published vessel count (verified July 2026); None
# where the source has no fixed size (e.g. detentions accrue continuously).
SOURCE_META = {
    "ofac": ("OFAC SDN (US Treasury)", None, "Public domain", True,
             "No clean vessel-only total published; ~900+ vessel entries across programs"),
    "gur": ("Ukraine GUR shadow fleet", 1404, "CC BY-NC", True,
            "Largest structured list; mirrors the ~1,400 global shadow-fleet estimate"),
    "eu": ("EU Annex XLII port ban", 600, "Public sector", True,
           "~600 after +41 in the Dec 2025 package"),
    "uani": ("UANI Ghost Armada (Iran)", 573, "NGO", True,
             "Full 6-page list scraped from the Archive incl. delisted historicals"),
    "uk": ("UK FCDO Sanctions List", 657, "Open Gov Licence", True,
           "Authoritative UK register incl. specified ships; ~621 Russia + DPRK"),
    "parismou": ("Paris MoU port ban", None, "CC BY-NC", True,
                 "Live banning list, typically dozens"),
    "tokyomou": ("Tokyo MoU detentions", None, "CC BY-NC", True,
                 "~1,189 ships detained per year; corroboration signal, not a designation"),
    "canada": ("Canada SEMA", None, "CC BY-NC", True,
               "Mostly mirrors Russia/DPRK designations; adds some net-new hulls"),
    "australia": ("Australia DFAT", None, "CC BY-NC", True,
                  "Largely mirrors allied Russia/DPRK lists"),
    "switzerland": ("Switzerland SECO", None, "CC BY-NC", True,
                    "Tracks EU designations closely; some net-new"),
    "iuu": ("Combined IUU fishing list", None, "Non-commercial", True,
            "All RFMO fishing blacklists (CCAMLR/ICCAT/...); ~66 with IMO"),
    "eu_iuu": ("EU IUU vessel list", None, "Official EU / free reuse", True,
               "EU Implementing Regulation adopting the RFMO fishing blacklists; "
               "official EU law so commercial-safe; ~48 with IMO"),
    "un1718": ("UN Security Council 1718 (DPRK)", None, "UN public", True,
               "All 58 IMO-bearing North Korea designations (1 of the 59 has no IMO to match)"),
    "blackseamou": ("Black Sea MoU detentions", None, "CC BY-NC", True,
                    "PSC detentions in Black/Azov Sea shadow-fleet waters; corroboration"),
    "abujamou": ("Abuja MoU detentions", None, "CC BY-NC", True,
                 "PSC detentions in W/C Africa (narco/oil-theft/IUU); corroboration"),
}
# best current estimate of the total global shadow/dark fleet (RUSI/Windward)
GLOBAL_FLEET_ESTIMATE = 1400


@router.get("/risklists/status")
async def risklists_status(session: AsyncSession = Depends(get_session)):
    rows = {
        s: (c, ts) for s, c, ts in (await session.execute(
            select(RiskListEntry.source, func.count(), func.max(RiskListEntry.imported_at))
            .group_by(RiskListEntry.source)
        )).all()
    }
    unique = await session.scalar(select(func.count(func.distinct(RiskListEntry.imo))))
    # the meaningful figure: unique SANCTIONED/BANNED vessels, excluding the
    # corroboration-only detention lists (Tokyo MoU = substandard ships, not
    # designations) which otherwise triple the headline count
    corroboration = {"tokyomou", "blackseamou", "abujamou"}
    sanctioned_unique = await session.scalar(
        select(func.count(func.distinct(RiskListEntry.imo)))
        .where(RiskListEntry.source.notin_(corroboration))
    )
    # role drives how each source is presented: a designation counts toward
    # coverage; a detention is corroboration only; fishing is its own track.
    roles = {"tokyomou": "detention", "blackseamou": "detention", "abujamou": "detention",
             "iuu": "fishing", "eu_iuu": "fishing"}
    out = []
    for src, (name, official, lic, auto, note) in SOURCE_META.items():
        collected, ts = rows.get(src, (0, None))
        role = roles.get(src, "designation")
        out.append({
            "source": src, "name": name, "collected": collected,
            "official_total": official, "license": lic, "auto_updates": auto,
            "note": note, "role": role, "imported_at": ts,
        })
    for src, (collected, ts) in rows.items():
        if src not in SOURCE_META:
            out.append({"source": src, "name": src.upper(), "collected": collected,
                        "official_total": None, "license": "custom import",
                        "auto_updates": False, "note": "ad-hoc CSV import",
                        "role": "designation", "imported_at": ts})
    # order: designations first, then fishing, then detention corroboration
    order = {"designation": 0, "fishing": 1, "detention": 2}
    out.sort(key=lambda s: (order.get(s["role"], 0), -s["collected"]))
    return {"sources": out, "unique_vessels": unique or 0,
            "sanctioned_unique": sanctioned_unique or 0,
            "global_fleet_estimate": GLOBAL_FLEET_ESTIMATE}


@router.post("/risklists/refresh")
async def risklists_refresh():
    results = await import_all(force=True)
    if not results:
        raise HTTPException(502, "All risk-list imports failed - see backend logs")
    return results


class CsvImport(BaseModel):
    source: str  # e.g. "uani", "kse", "c4ads"
    csv: str     # header row with at least "imo"; optional: name, flag, program


@router.post("/risklists/import-csv")
async def import_csv(payload: CsvImport, session: AsyncSession = Depends(get_session)):
    """Import a curated vessel list (KSE tanker tracker, UANI shadow-fleet
    tracker, C4ADS report annexes, …) - paste as CSV with an 'imo' column.
    Replaces previous entries of the same source; matches run within 10 min."""
    import csv as csv_mod
    import io
    from datetime import datetime, timezone

    from sqlalchemy import delete

    source = payload.source.strip().lower()
    if not source or source == "ofac":
        raise HTTPException(400, "Source must be a name other than 'ofac'")
    reader = csv_mod.DictReader(io.StringIO(payload.csv))
    if not reader.fieldnames or "imo" not in [f.strip().lower() for f in reader.fieldnames]:
        raise HTTPException(400, "CSV needs a header row with an 'imo' column")

    now = datetime.now(timezone.utc)
    entries = []
    for row in reader:
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        imo = "".join(ch for ch in row.get("imo", "") if ch.isdigit())
        if len(imo) != 7:
            continue
        entries.append(RiskListEntry(
            source=source, imo=imo, name=row.get("name") or None,
            flag=row.get("flag") or None, program=row.get("program") or None,
            imported_at=now,
        ))
    if not entries:
        raise HTTPException(400, "No rows with a valid 7-digit IMO found")

    await session.execute(delete(RiskListEntry).where(RiskListEntry.source == source))
    session.add_all(entries)
    await session.commit()
    return {"source": source, "imported": len(entries)}
