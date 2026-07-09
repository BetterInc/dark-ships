"""Behaviour engine: finds risky vessels in the live feed itself.

Rules (each fires a scored risk_event, deduplicated):
  A1 regional_gap      - went silent INSIDE a covered region while underway,
                         away from the bbox edge (edge silence = sailed out)
  A2 identity_change   - transmitted a different name/IMO/callsign than before
     midsea_appearance - brand-new MMSI first seen in open water, not near a
                         region edge (the Arconian re-identity trick)
  A3 impossible_jump   - consecutive positions implying > 40 kn = AIS spoofing
  A4 rendezvous        - two ships < 500 m apart, near-stationary, offshore,
                         both recently underway = possible ship-to-ship transfer
  B  risklist_*        - the vessel's IMO appears on an imported risk list

A vessel's score is the sum of its events over the last 30 days. With
AUTO_WATCHLIST on, high scorers fill the free watchlist slots (manual entries
are pinned and never touched; lowest-scoring auto entries get evicted first).
"""

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from ..config import get_settings
from ..db import SessionLocal
from ..geo import haversine_km, interior_distance_km
from ..models import (DraughtChange, IdentityChange, OilSlick, Position, RiskEvent,
                      RiskListEntry, Vessel, VesselRegistry)
from .coverage import covered_since, prune_heartbeats

logger = logging.getLogger(__name__)

WEIGHTS = {
    "regional_gap": 30.0,
    "identity_change": 40.0,
    "midsea_appearance": 15.0,  # corroboration-only: below the suggestion floor (25),
                                # so a lone mid-sea appearance never surfaces a vessel
                                # by itself - it only adds weight to one already flagged
    "impossible_jump": 35.0,
    "rendezvous": 25.0,
    "drift_mismatch": 30.0,
    "oil_slick": 45.0,
    "dark_association": 20.0,
    "loitering": 30.0,
    "mmsi_collision": 45.0,
    "circle_spoofing": 40.0,
    "identity_integrity": 30.0,
    "flag_hop": 35.0,
    "nav_status_lie": 30.0,
    # GFW event corroboration (behavioural signals, similar tier to
    # rendezvous/loitering). CC BY-NC source, see services/gfw.py.
    "gfw_encounter": 30.0,
    "gfw_ais_gap": 30.0,
    "gfw_loitering": 25.0,
    # Tanker draught swung laden<->ballast offshore, corroborated (STS zone or a
    # nearby rendezvous/encounter). Strong, low-false-positive - like oil_slick.
    "draught_change": 45.0,
    "risklist_ofac": 100.0,
    "risklist_iuu": 80.0,
    "risklist_eu_iuu": 80.0,
}

# AIS navigational-status codes. Broadcasting "at anchor"/"moored" while
# actually steaming is a deliberate deception (look innocent to filters).
NAV_STOPPED = {1, 5}       # 1 = at anchor, 5 = moored
NAV_LIE_MIN_SOG = 3.0      # clearly moving, not anchor-swing
NAV_LIE_COOLDOWN_HOURS = 12

# ITU maritime identity digits: country codes occupy 201-775 (first digit 2-7
# is a ship). MMSIs outside that, factory defaults, or repeated digits are
# fabricated - a core C4ADS identity-laundering signature.
VALID_MID_MIN, VALID_MID_MAX = 201, 775


def _mmsi_integrity_issue(mmsi: int) -> str | None:
    """MMSI-only integrity checks (clean, no false positives). IMO-checksum is
    deliberately NOT here - inland barges broadcast an 8-digit ENI in the IMO
    field that fails the sea-going checksum, and ingest already nulls genuinely
    invalid IMOs, so a registry IMO-checksum check just re-flags canal barges."""
    s = str(mmsi)
    if len(s) != 9:
        return f"malformed MMSI ({len(s)} digits)"
    if len(set(s)) == 1 or s.endswith("0000000"):
        return "factory-default MMSI"
    mid = int(s[:3])
    if not (VALID_MID_MIN <= mid <= VALID_MID_MAX):
        return f"invalid country prefix (MID {mid})"
    return None


def _valid_imo(imo: str) -> bool:
    if not imo or len(imo) != 7 or not imo.isdigit():
        return False
    return sum(int(d) * w for d, w in zip(imo[:6], range(7, 1, -1))) % 10 == int(imo[6])

# MMSI collision: one identity transmitting two coexisting tracks = a shared /
# cloned identity (two physical hulls on one MMSI). Distinct from impossible_jump
# (a single track teleporting) - here two tracks run in parallel.
COLLISION_WINDOW_MIN = 30
COLLISION_MIN_KM = 40.0        # clusters this far apart can't be one ship
COLLISION_MIN_PER_CLUSTER = 2  # each side needs corroboration (not a lone glitch)

# Circle spoofing: GNSS injection draws a geometric circle while the ship works
# elsewhere. Signature: claims to be moving (SOG up) yet goes nowhere, on a
# near-perfect circle - which anchor-swing (SOG~0) does not do.
CIRCLE_WINDOW_HOURS = 6
CIRCLE_MIN_POINTS = 12
CIRCLE_MIN_SOG = 4.0
CIRCLE_RADIUS_CV_MAX = 0.30    # low radial variance = circular
CIRCLE_MIN_RADIUS_KM = 0.4     # bigger than an anchor swing
CIRCLE_MAX_RADIUS_KM = 12.0

LOITER_MAX_SOG = 2.0        # near-stationary
LOITER_MIN_HOURS = 4.0      # sustained dwell
LOITER_MAX_RADIUS_KM = 8.0  # holding position, not steaming through
LOITER_MIN_INTERIOR_KM = 25.0  # well offshore, away from ports at the box edge
LOITER_COOLDOWN_HOURS = 24
RISKLIST_DEFAULT_WEIGHT = 80.0  # custom imported lists (KSE, UANI, C4ADS, …)
RISKLIST_WEIGHTS = {            # per-source overrides for the default above
    "risklist_ofac": 100.0,
    "risklist_parismou": 70.0,   # refused EU port access - strong, not a full sanction
    # PSC detention = substandard ship (old/poorly maintained), NOT proof of
    # crime, and there are thousands. Kept BELOW the auto-add threshold so it
    # corroborates other signals (a detained ship that also loiters/goes dark
    # stacks past 50) without flooding the watchlist on its own.
    "risklist_tokyomou": 25.0,
    "risklist_blackseamou": 25.0,
    "risklist_abujamou": 25.0,
}

SCORE_WINDOW_DAYS = 30
JUMP_COOLDOWN_HOURS = 6

# Draught-change (at-sea cargo transfer) guardrails. A laden<->ballast swing is
# metres, not a trim adjustment; the feed has junk draughts (0/tiny/>25) that
# must be dropped; and corroboration is required to keep false positives ~zero.
DRAUGHT_MIN_M, DRAUGHT_MAX_M = 1.0, 25.0
DRAUGHT_STS_DELTA = 2.5
DRAUGHT_LOOKBACK_DAYS = 7
DRAUGHT_POS_WINDOW_HOURS = 6      # how far from the change ts a position may be
DRAUGHT_CORROB_HOURS = 48        # rendezvous/encounter must be this recent
DRAUGHT_CORROB_KM = 50.0         # ...and this close to the change location
DRAUGHT_COOLDOWN_HOURS = 48
RENDEZVOUS_COOLDOWN_HOURS = 12
RENDEZVOUS_MAX_KM = 0.5
RENDEZVOUS_MAX_SOG = 2.0
RECENTLY_UNDERWAY_SOG = 5.0


def _bboxes():
    return [r.bbox for r in get_settings().ais_regions]


# Busy port approaches / anchorages where two tankers sitting close together, or
# a ship dropping off AIS for a bit, is routine (waiting for a berth, pilot, or
# lightering) - NOT ship-to-ship transfer or going dark. Rendezvous and
# regional-gap flags are suppressed inside these boxes to cut false positives.
# (name, lat_min, lat_max, lon_min, lon_max)
PORT_ZONES = [
    ("Rotterdam/Europoort", 51.85, 52.10, 3.75, 4.55),
    ("Antwerp/Scheldt",     51.20, 51.45, 4.20, 4.50),
    ("IJmuiden/Amsterdam",  52.35, 52.55, 4.50, 4.95),
]


def _in_port_zone(lat: float, lon: float) -> bool:
    return any(la0 <= lat <= la1 and lo0 <= lon <= lo1
               for _, la0, la1, lo0, lo1 in PORT_ZONES)


def _in_sts_region(lat: float, lon: float) -> bool:
    """True if the point sits inside a region tagged kind=='sts' (Eastern Med,
    Port Said/Suez, Hormuz, Singapore & Riau) - where at-sea cargo transfer is
    the known activity. bbox is [[latMin, lonMin], [latMax, lonMax]]."""
    for r in get_settings().ais_regions:
        if r.kind != "sts":
            continue
        (lat_min, lon_min), (lat_max, lon_max) = r.bbox
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return True
    return False


async def _has_event(session, mmsi: int, rule: str, since: datetime | None) -> bool:
    q = select(RiskEvent.id).where(RiskEvent.mmsi == mmsi, RiskEvent.rule == rule)
    if since is not None:
        q = q.where(RiskEvent.ts >= since)
    return (await session.execute(q.limit(1))).first() is not None


def _event(mmsi: int, rule: str, **details) -> RiskEvent:
    if rule in WEIGHTS:
        score = WEIGHTS[rule]
    elif rule.startswith("risklist_"):
        score = RISKLIST_WEIGHTS.get(rule, RISKLIST_DEFAULT_WEIGHT)
    else:
        score = RISKLIST_DEFAULT_WEIGHT
    return RiskEvent(mmsi=mmsi, rule=rule, ts=datetime.now(timezone.utc),
                     score=score, details=json.dumps(details, default=str))


async def _latest_region_positions(session, now: datetime, since_hours: float = 24):
    since = now - timedelta(hours=since_hours)
    latest = (
        select(Position)
        .where(Position.ts >= since, Position.source == "region")
        .distinct(Position.mmsi)
        .order_by(Position.mmsi, Position.ts.desc())
        .subquery()
    )
    return (await session.execute(select(latest))).all()


async def rule_regional_gaps(session, now: datetime) -> list[RiskEvent]:
    s = get_settings()
    bboxes = _bboxes()
    listening_since = await covered_since(session)
    events = []
    for p in await _latest_region_positions(session, now):
        if p.ts < listening_since:
            continue  # silence started while WE were offline - not evidence
        if now - p.ts < timedelta(hours=s.regional_gap_hours):
            continue  # still transmitting recently
        if (p.sog or 0) < s.regional_gap_min_sog:
            continue  # was drifting/moored, not underway
        if interior_distance_km(p.lat, p.lon, bboxes) < s.edge_margin_km:
            continue  # near the edge: probably just left the box
        if _in_port_zone(p.lat, p.lon):
            continue  # last seen in a port approach - berthing/coverage, not dark
        if await _has_event(session, p.mmsi, "regional_gap", p.ts):
            continue
        events.append(_event(p.mmsi, "regional_gap", last_ts=p.ts, lat=p.lat,
                             lon=p.lon, sog=p.sog, ship_name=p.ship_name))
    return events


async def rule_identity_changes(session) -> list[RiskEvent]:
    changes = (await session.execute(
        select(IdentityChange).where(IdentityChange.scored.is_(False))
    )).scalars().all()
    events = []
    for c in changes:
        events.append(_event(c.mmsi, "identity_change", field=c.field,
                             old=c.old_value, new=c.new_value, ts=c.ts))
        c.scored = True
    return events


async def rule_midsea_appearance(session, now: datetime) -> list[RiskEvent]:
    s = get_settings()
    oldest = await session.scalar(select(func.min(Position.ts)))
    if oldest is None or now - oldest < timedelta(hours=24):
        return []  # feed too young: everything looks "new" on day one
    bboxes = _bboxes()
    fresh = (await session.execute(
        select(VesselRegistry).where(VesselRegistry.first_seen >= now - timedelta(minutes=30))
    )).scalars().all()
    events = []
    for v in fresh:
        if v.first_lat is None or v.first_lon is None:
            continue
        # Only real seagoing vessels: cargo/tanker (70-89) or fishing (30-39).
        # ~90% of "appearances" are no-type small craft (harbour launches, barges)
        # switching their AIS on at a berth - meaningless. The identity-switch
        # trick this rule targets is about big ships, so require a known hull type.
        if v.ship_type is None or not (30 <= v.ship_type <= 39 or 70 <= v.ship_type <= 89):
            continue
        if interior_distance_km(v.first_lat, v.first_lon, bboxes) < s.edge_margin_km:
            continue  # appeared at the edge: just sailed into the box
        if _in_port_zone(v.first_lat, v.first_lon):
            continue  # busy port/anchorage - powering on AIS, not a dark appearance
        if await _has_event(session, v.mmsi, "midsea_appearance", None):
            continue
        events.append(_event(v.mmsi, "midsea_appearance", first_seen=v.first_seen,
                             lat=v.first_lat, lon=v.first_lon, name=v.name))
    return events


async def rule_impossible_jumps(session, now: datetime) -> list[RiskEvent]:
    s = get_settings()
    rows = (await session.execute(
        select(Position.mmsi, Position.ts, Position.lat, Position.lon)
        .where(Position.ts >= now - timedelta(minutes=20))
        .order_by(Position.mmsi, Position.ts)
    )).all()
    events = []
    i, n = 0, len(rows)
    while i < n:
        j = i
        while j < n and rows[j].mmsi == rows[i].mmsi:
            j += 1
        pts, mmsi = rows[i:j], rows[i].mmsi
        i = j
        # need a position AFTER the jump to tell a real relocation from a glitch
        if len(pts) < 3:
            continue
        for k in range(1, len(pts) - 1):
            a, b, c = pts[k - 1], pts[k], pts[k + 1]
            dt_h = (b.ts - a.ts).total_seconds() / 3600
            if dt_h < 2 / 60:  # ignore sub-2-minute deltas (GPS jitter)
                continue
            speed_kn = haversine_km(a.lat, a.lon, b.lat, b.lon) / 1.852 / dt_h
            if speed_kn <= s.impossible_speed_knots:
                continue
            # A single corrupt fix snaps straight back to the real track: the
            # next point C is then nearer the pre-jump point A than the jumped-to
            # point B. A genuine (spoofed) relocation is sustained - C stays near
            # B. Only the sustained case is a real impossible jump; a lone
            # outlier (implied 1000s of knots) is a garbled coordinate, not spoofing.
            if haversine_km(b.lat, b.lon, c.lat, c.lon) > haversine_km(a.lat, a.lon, c.lat, c.lon):
                continue
            if not await _plausibly_fast(session, mmsi, now) and \
               not await _has_event(session, mmsi, "impossible_jump",
                                    now - timedelta(hours=JUMP_COOLDOWN_HOURS)):
                events.append(_event(mmsi, "impossible_jump",
                                     implied_kn=round(speed_kn, 1),
                                     from_=[a.lat, a.lon], to=[b.lat, b.lon]))
            break
    return events


async def _plausibly_fast(session, mmsi: int, now: datetime) -> bool:
    """Audit finding: the false positives were high-speed craft (which really
    do 40+ kn) and shared MMSIs on small craft. A jump only means spoofing on
    a ship that is otherwise SLOW - so skip HSC types and any vessel that has
    genuinely logged high speed itself."""
    ship_type = await session.scalar(
        select(VesselRegistry.ship_type).where(VesselRegistry.mmsi == mmsi))
    if ship_type is not None and 40 <= ship_type <= 49:  # high-speed craft
        return True
    max_sog = await session.scalar(
        select(func.max(Position.sog)).where(
            Position.mmsi == mmsi, Position.ts >= now - timedelta(days=7)))
    return max_sog is not None and max_sog > 30.0


async def rule_rendezvous(session, now: datetime) -> list[RiskEvent]:
    s = get_settings()
    bboxes = _bboxes()
    candidates = [
        p for p in await _latest_region_positions(session, now, since_hours=0.5)
        if (p.sog or 99) <= RENDEZVOUS_MAX_SOG
        and interior_distance_km(p.lat, p.lon, bboxes) >= s.rendezvous_margin_km
    ]
    if len(candidates) < 2:
        return []

    # Audit finding: cargo-type (70-79) pairs were all routine - car carriers,
    # coasters, tugs misbroadcasting cargo type. Real STS oil transfers are
    # TANKER-to-TANKER (80-89), so both ships must be tankers.
    tankers = {
        row[0] for row in await session.execute(
            select(VesselRegistry.mmsi).where(
                VesselRegistry.mmsi.in_([p.mmsi for p in candidates]),
                VesselRegistry.ship_type.between(80, 89),
            )
        )
    }
    candidates = [p for p in candidates if p.mmsi in tankers]
    if len(candidates) < 2:
        return []

    # grid-bucket to avoid O(n²) over the whole sea
    grid: dict[tuple[int, int], list] = defaultdict(list)
    for p in candidates:
        grid[(int(p.lat * 100), int(p.lon * 100))].append(p)

    pairs = []
    for (glat, glon), items in grid.items():
        neighbours = [q for dlat in (-1, 0, 1) for dlon in (-1, 0, 1)
                      for q in grid.get((glat + dlat, glon + dlon), [])]
        for p in items:
            for q in neighbours:
                if q.mmsi <= p.mmsi:
                    continue
                if haversine_km(p.lat, p.lon, q.lat, q.lon) <= RENDEZVOUS_MAX_KM:
                    pairs.append((p, q))

    if not pairs:
        return []

    # both must have been genuinely underway recently, or it's just an anchorage
    involved = {m for p, q in pairs for m in (p.mmsi, q.mmsi)}
    underway = {
        row[0] for row in await session.execute(
            select(Position.mmsi).where(
                Position.mmsi.in_(involved),
                Position.ts >= now - timedelta(hours=6),
                Position.sog >= RECENTLY_UNDERWAY_SOG,
            ).distinct()
        )
    }

    events = []
    cooldown = now - timedelta(hours=RENDEZVOUS_COOLDOWN_HOURS)
    for p, q in pairs:
        if p.mmsi not in underway or q.mmsi not in underway:
            continue
        if _in_port_zone(p.lat, p.lon) or _in_port_zone(q.lat, q.lon):
            continue  # Rotterdam/Antwerp/IJmuiden approach - berthing queue, not STS
        for a, b in ((p, q), (q, p)):
            if not await _has_event(session, a.mmsi, "rendezvous", cooldown):
                events.append(_event(a.mmsi, "rendezvous", other_mmsi=b.mmsi,
                                     other_name=b.ship_name, lat=a.lat, lon=a.lon))
    return events


async def rule_oil_slick_corroboration(session, now: datetime) -> list[RiskEvent]:
    """A Cerulean-detected slick at a recent rendezvous location upgrades a
    'possible STS' into near-conclusive evidence. Sentinel-1 revisit lags, so
    the window is generous."""
    recent = (await session.execute(
        select(RiskEvent).where(RiskEvent.rule == "rendezvous",
                                RiskEvent.ts >= now - timedelta(days=7))
    )).scalars().all()
    events = []
    for rdv in recent:
        details = json.loads(rdv.details or "{}")
        lat, lon = details.get("lat"), details.get("lon")
        if lat is None or lon is None:
            continue
        if await _has_event(session, rdv.mmsi, "oil_slick", rdv.ts):
            continue
        slicks = (await session.execute(
            select(OilSlick).where(
                OilSlick.lat.between(lat - 0.1, lat + 0.1),
                OilSlick.lon.between(lon - 0.15, lon + 0.15),
                OilSlick.ts.between(rdv.ts - timedelta(hours=12), rdv.ts + timedelta(days=4)),
            ).limit(5)
        )).scalars().all()
        near = [s for s in slicks if haversine_km(lat, lon, s.lat, s.lon) <= 5.0]
        if near:
            slick = near[0]
            events.append(_event(rdv.mmsi, "oil_slick", slick_id=slick.id,
                                 slick_ts=slick.ts, area_m2=slick.area_m2,
                                 rendezvous_with=details.get("other_mmsi")))
            logger.warning("Oil slick corroborates rendezvous: MMSI %s, slick %s",
                           rdv.mmsi, slick.id)
    return events


async def _position_near_ts(session, mmsi: int, ts: datetime, window_hours: float):
    """The vessel's recorded Position closest in time to `ts`, within
    +/- window_hours. Returns the Position row, or None if we can't corroborate
    a location for the change (statics carry no lat/lon)."""
    rows = (await session.execute(
        select(Position.lat, Position.lon, Position.ts).where(
            Position.mmsi == mmsi,
            Position.ts >= ts - timedelta(hours=window_hours),
            Position.ts <= ts + timedelta(hours=window_hours),
        )
    )).all()
    if not rows:
        return None
    return min(rows, key=lambda r: abs((r.ts - ts).total_seconds()))


async def rule_draught_change(session, now: datetime) -> list[RiskEvent]:
    """A tanker whose reported draught swings laden<->ballast while offshore has
    transferred cargo at sea (STS). Draught is in the AIS static feed; this rule
    turns a recorded DraughtChange into a scored event ONLY when every low-false-
    positive guardrail passes: tanker type, plausible+significant change, a
    resolvable non-port location, and independent corroboration (an STS zone or a
    nearby rendezvous/encounter)."""
    changes = (await session.execute(
        select(DraughtChange).where(
            DraughtChange.ts >= now - timedelta(days=DRAUGHT_LOOKBACK_DAYS)
        ).order_by(DraughtChange.ts)
    )).scalars().all()
    if not changes:
        return []

    # tanker filter (80-89) in one query
    mmsis = {c.mmsi for c in changes}
    tankers = {
        row[0] for row in await session.execute(
            select(VesselRegistry.mmsi).where(
                VesselRegistry.mmsi.in_(mmsis),
                VesselRegistry.ship_type.between(80, 89),
            )
        )
    }

    events = []
    cooldown = now - timedelta(hours=DRAUGHT_COOLDOWN_HOURS)
    for c in changes:
        if c.mmsi not in tankers:
            continue  # draught STS is an oil-tanker signal
        old, new = c.old_draught, c.new_draught
        if not (DRAUGHT_MIN_M <= old <= DRAUGHT_MAX_M) or \
           not (DRAUGHT_MIN_M <= new <= DRAUGHT_MAX_M):
            continue  # unset / garbage draught (the feed has 0.0-0.8 junk)
        delta = abs(new - old)
        if delta < DRAUGHT_STS_DELTA:
            continue  # a trim adjustment, not a laden<->ballast swing
        if await _has_event(session, c.mmsi, "draught_change", cooldown):
            continue
        pos = await _position_near_ts(session, c.mmsi, c.ts, DRAUGHT_POS_WINDOW_HOURS)
        if pos is None:
            continue  # cannot corroborate a location - drop it
        if _in_port_zone(pos.lat, pos.lon):
            continue  # draught change at a port is normal loading/discharge

        # corroboration (at least one), else skip - this keeps FPs near zero
        corroboration = None
        if _in_sts_region(pos.lat, pos.lon):
            corroboration = "sts_zone"
        else:
            nearby = (await session.execute(
                select(RiskEvent).where(
                    RiskEvent.mmsi == c.mmsi,
                    RiskEvent.rule.in_(("rendezvous", "gfw_encounter")),
                    RiskEvent.ts >= c.ts - timedelta(hours=DRAUGHT_CORROB_HOURS),
                    RiskEvent.ts <= c.ts + timedelta(hours=DRAUGHT_CORROB_HOURS),
                )
            )).scalars().all()
            for ev in nearby:
                d = json.loads(ev.details or "{}")
                elat, elon = d.get("lat"), d.get("lon")
                if elat is None or elon is None:
                    continue
                if haversine_km(pos.lat, pos.lon, elat, elon) <= DRAUGHT_CORROB_KM:
                    corroboration = "rendezvous"
                    break
        if corroboration is None:
            continue

        events.append(_event(c.mmsi, "draught_change",
                             old_draught=round(old, 1), new_draught=round(new, 1),
                             delta=round(delta, 1), lat=pos.lat, lon=pos.lon,
                             corroboration=corroboration))
        logger.warning("Draught change at sea: MMSI %s %.1f->%.1f m (%s) at %.2f,%.2f",
                       c.mmsi, old, new, corroboration, pos.lat, pos.lon)
    return events


# NOTE: a "bogus IMO range" rule (1xxxxxx = yacht registry) was tried and
# DROPPED: live data showed mainstream 2023+ newbuilds (MSC, Navios, Wilson)
# legitimately carry 1xxxxxx IMOs - issuance rolled into that range. IMO
# plausibility needs an external registry lookup, not a range check.


async def rule_dark_association(session, now: datetime) -> list[RiskEvent]:
    """An unlisted tanker anchored within 10 km of 3+ sanctioned ships in a
    hotspot region. Circumstantial (hence the low weight), but the EOPL
    anchorage shows exactly this is where tomorrow's designations sit today."""
    sanctioned = {
        row[0] for row in await session.execute(
            select(RiskEvent.mmsi).where(RiskEvent.rule.like("risklist_%")).distinct()
        )
    }
    if len(sanctioned) < 3:
        return []
    latest = await _latest_region_positions(session, now, since_hours=3)
    sanc_pos = [p for p in latest if p.mmsi in sanctioned]
    tankers = {
        row[0] for row in await session.execute(
            select(VesselRegistry.mmsi).where(
                VesselRegistry.mmsi.in_([p.mmsi for p in latest]),
                VesselRegistry.ship_type.between(80, 89),
            )
        )
    }
    events = []
    for p in latest:
        if p.mmsi in sanctioned or p.mmsi not in tankers or (p.sog or 99) > 2.0:
            continue
        near = sum(1 for s in sanc_pos if abs(s.lat - p.lat) < 0.15
                   and abs(s.lon - p.lon) < 0.15
                   and haversine_km(p.lat, p.lon, s.lat, s.lon) <= 10.0)
        if near >= 3 and not await _has_event(session, p.mmsi, "dark_association", None):
            events.append(_event(p.mmsi, "dark_association", sanctioned_nearby=near,
                                 lat=p.lat, lon=p.lon, ship_name=p.ship_name))
    return events


async def rule_loitering(session, now: datetime) -> list[RiskEvent]:
    """A single vessel holding position offshore in a TRANSIT corridor (not an
    STS anchorage) - the mothership / dead-drop / fishing-mothership signature
    on the Atlantic cocaine routes. Distinct from rendezvous (needs two ships)
    and gap (needs silence): here the ship keeps transmitting but stops going
    anywhere, far from any port."""
    transit_bboxes = [r.bbox for r in get_settings().ais_regions if r.kind == "transit"]
    if not transit_bboxes:
        return []
    since = now - timedelta(hours=LOITER_MIN_HOURS)

    # candidate MMSIs: seen in-region, currently slow
    recent = [p for p in await _latest_region_positions(session, now, since_hours=1)
              if (p.sog or 99) <= LOITER_MAX_SOG
              and interior_distance_km(p.lat, p.lon, transit_bboxes) >= LOITER_MIN_INTERIOR_KM]
    events = []
    for p in recent:
        if await _has_event(session, p.mmsi, "loitering", now - timedelta(hours=LOITER_COOLDOWN_HOURS)):
            continue
        track = (await session.execute(
            select(Position).where(Position.mmsi == p.mmsi, Position.ts >= since)
            .order_by(Position.ts)
        )).scalars().all()
        if len(track) < 3:
            continue  # too little history to confirm a sustained dwell
        span_h = (track[-1].ts - track[0].ts).total_seconds() / 3600
        if span_h < LOITER_MIN_HOURS:
            continue
        lat_c = sum(t.lat for t in track) / len(track)
        lon_c = sum(t.lon for t in track) / len(track)
        radius = max(haversine_km(lat_c, lon_c, t.lat, t.lon) for t in track)
        if radius > LOITER_MAX_RADIUS_KM:
            continue  # drifting through, not holding
        moved = max((t.sog or 0) for t in track)
        if moved > 8.0:
            continue  # had a real transit leg in the window - just passing
        events.append(_event(p.mmsi, "loitering", hours=round(span_h, 1),
                             radius_km=round(radius, 1), lat=round(lat_c, 3),
                             lon=round(lon_c, 3), ship_name=p.ship_name))
        logger.info("Loitering: MMSI %s held position %.0fh within %.1f km at %.2f,%.2f",
                    p.mmsi, span_h, radius, lat_c, lon_c)
    return events


async def rule_mmsi_collision(session, now: datetime) -> list[RiskEvent]:
    """One MMSI reporting two far-apart positions within the same short window
    = two hulls sharing an identity (cloning / laundering). GFW's 'one identity
    shared by multiple vessels' pattern. Positions-only, near-zero false
    positives - two ships genuinely cannot be one."""
    since = now - timedelta(minutes=COLLISION_WINDOW_MIN)
    rows = (await session.execute(
        select(Position.mmsi, Position.ts, Position.lat, Position.lon)
        .where(Position.ts >= since)
        .order_by(Position.mmsi, Position.ts)
    )).all()

    by_mmsi: dict[int, list] = defaultdict(list)
    for r in rows:
        by_mmsi[r.mmsi].append(r)

    events = []
    for mmsi, pts in by_mmsi.items():
        if len(pts) < 2 * COLLISION_MIN_PER_CLUSTER:
            continue
        # anchor cluster A on the first point; cluster B = points far from A
        a = pts[0]
        far = [p for p in pts if haversine_km(a.lat, a.lon, p.lat, p.lon) > COLLISION_MIN_KM]
        near = [p for p in pts if p not in far]
        if len(far) < COLLISION_MIN_PER_CLUSTER or len(near) < COLLISION_MIN_PER_CLUSTER:
            continue
        # the two clusters must coexist in time, not be a sequential move
        near_span = (max(p.ts for p in near), min(p.ts for p in near))
        far_span = (max(p.ts for p in far), min(p.ts for p in far))
        if far_span[1] > near_span[0] or near_span[1] > far_span[0]:
            continue  # time ranges don't overlap → could be one ship moving
        if await _has_event(session, mmsi, "mmsi_collision", since):
            continue
        b = far[0]
        events.append(_event(mmsi, "mmsi_collision",
                             cluster_a=[round(a.lat, 3), round(a.lon, 3)],
                             cluster_b=[round(b.lat, 3), round(b.lon, 3)],
                             km_apart=round(haversine_km(a.lat, a.lon, b.lat, b.lon), 1)))
        logger.warning("MMSI collision: %s transmitting from two places %.0f km apart",
                       mmsi, haversine_km(a.lat, a.lon, b.lat, b.lon))
    return events


async def rule_circle_spoofing(session, now: datetime) -> list[RiskEvent]:
    """A track that draws a near-perfect circle while claiming to move (SOG up)
    but going nowhere = GNSS/circle spoofing (the C4ADS Shanghai pattern).
    Anchor-swing looks similar but reports SOG ~0, so the moving-yet-static
    contradiction is the discriminator."""
    import math
    since = now - timedelta(hours=CIRCLE_WINDOW_HOURS)
    # the 'watchlist' source is retired; every ship is recorded region/world at
    # 1/min. Scope this to the active watchlist vessels (as before it ran on the
    # followed set), which also keeps the per-point scan cheap.
    watch_mmsis = select(Vessel.mmsi).where(Vessel.active.is_(True)).scalar_subquery()
    rows = (await session.execute(
        select(Position.mmsi, Position.lat, Position.lon, Position.sog, Position.ts)
        .where(Position.ts >= since, Position.mmsi.in_(watch_mmsis))
        .order_by(Position.mmsi, Position.ts)
    )).all()
    by_mmsi: dict[int, list] = defaultdict(list)
    for r in rows:
        by_mmsi[r.mmsi].append(r)

    events = []
    for mmsi, pts in by_mmsi.items():
        if len(pts) < CIRCLE_MIN_POINTS:
            continue
        avg_sog = sum((p.sog or 0) for p in pts) / len(pts)
        if avg_sog < CIRCLE_MIN_SOG:
            continue  # not claiming to move → anchor swing, not spoofing
        lat_c = sum(p.lat for p in pts) / len(pts)
        lon_c = sum(p.lon for p in pts) / len(pts)
        radii = [haversine_km(lat_c, lon_c, p.lat, p.lon) for p in pts]
        mean_r = sum(radii) / len(radii)
        if not (CIRCLE_MIN_RADIUS_KM <= mean_r <= CIRCLE_MAX_RADIUS_KM):
            continue
        cv = (math.sqrt(sum((r - mean_r) ** 2 for r in radii) / len(radii)) / mean_r)
        if cv > CIRCLE_RADIUS_CV_MAX:
            continue  # not circular
        net = haversine_km(pts[0].lat, pts[0].lon, pts[-1].lat, pts[-1].lon)
        if net > 2 * mean_r:
            continue  # actually went somewhere
        if await _has_event(session, mmsi, "circle_spoofing", since):
            continue
        events.append(_event(mmsi, "circle_spoofing", radius_km=round(mean_r, 1),
                             avg_sog=round(avg_sog, 1), lat=round(lat_c, 3), lon=round(lon_c, 3)))
        logger.warning("Circle spoofing: %s claims %.0f kn but circles a %.1f km "
                       "radius going nowhere", mmsi, avg_sog, mean_r)
    return events


async def rule_identity_integrity(session) -> list[RiskEvent]:
    """Fabricated identities: a cargo/tanker whose MMSI has an invalid country
    prefix, is a factory default, or whose IMO fails its check digit. Pure
    lookup, near-zero false positives - two ships genuinely can't have a valid
    identity and a broken one."""
    rows = (await session.execute(
        select(VesselRegistry.mmsi, VesselRegistry.imo, VesselRegistry.name)
        .where(VesselRegistry.ship_type.between(70, 89))
    )).all()
    events = []
    for mmsi, imo, name in rows:
        issue = _mmsi_integrity_issue(mmsi)
        if issue is None or await _has_event(session, mmsi, "identity_integrity", None):
            continue
        events.append(_event(mmsi, "identity_integrity", issue=issue, imo=imo, name=name))
        logger.warning("Identity integrity: MMSI %s (%s) - %s", mmsi, name, issue)
    return events


async def rule_flag_hopping(session) -> list[RiskEvent]:
    """One IMO transmitting under multiple MMSIs (different country prefixes) =
    the ship reflagged/changed identity - a top RUSI/Windball shadow-fleet
    marker. Detected from our registry: same IMO seen on 2+ MMSIs."""
    rows = (await session.execute(
        select(VesselRegistry.imo, VesselRegistry.mmsi, VesselRegistry.name)
        .where(VesselRegistry.imo.isnot(None))
    )).all()
    by_imo: dict[str, list] = defaultdict(list)
    for imo, mmsi, name in rows:
        # only REAL IMOs count. Dummy IMOs (9999999, 1234567, repeated digits)
        # are shared by many unrelated ships, so grouping on them fakes a
        # reflag. A valid check-digit means it's a genuine registered number.
        if not _valid_imo(imo) or len(set(imo)) == 1:
            continue
        by_imo[imo].append((mmsi, name))

    events = []
    for imo, entries in by_imo.items():
        mmsis = {m for m, _ in entries}
        if len(mmsis) < 2:
            continue
        mids = {str(m)[:3] for m in mmsis}  # distinct country prefixes = reflags
        name = next((n for _, n in entries if n), None)
        for mmsi in mmsis:
            if await _has_event(session, mmsi, "flag_hop", None):
                continue
            events.append(_event(mmsi, "flag_hop", imo=imo, name=name,
                                 mmsi_count=len(mmsis), flags=len(mids),
                                 all_mmsis=sorted(mmsis)))
        if len(mmsis) >= 2:
            logger.warning("Flag-hop: IMO %s (%s) under %d MMSIs / %d flags",
                           imo, name, len(mmsis), len(mids))
    return events


async def rule_nav_status_lie(session, now: datetime) -> list[RiskEvent]:
    """A ship broadcasting 'at anchor' or 'moored' while actually steaming
    (SOG > 3 kn). It's declaring itself stopped to look innocent to automated
    filters - a deliberate deception, not an equipment quirk."""
    latest = await _latest_region_positions(session, now, since_hours=2)
    events = []
    for p in latest:
        if p.nav_status in NAV_STOPPED and (p.sog or 0) > NAV_LIE_MIN_SOG:
            if await _has_event(session, p.mmsi, "nav_status_lie",
                                now - timedelta(hours=NAV_LIE_COOLDOWN_HOURS)):
                continue
            events.append(_event(p.mmsi, "nav_status_lie",
                                 declared="at anchor" if p.nav_status == 1 else "moored",
                                 actual_sog=p.sog, ship_name=p.ship_name))
            logger.warning("Nav-status lie: MMSI %s declares stopped but doing %.1f kn",
                           p.mmsi, p.sog)
    return events


async def rule_risklist_matches(session) -> list[RiskEvent]:
    rows = (await session.execute(
        select(VesselRegistry.mmsi, VesselRegistry.imo, VesselRegistry.name,
               RiskListEntry.source, RiskListEntry.name, RiskListEntry.program)
        .join(RiskListEntry, RiskListEntry.imo == VesselRegistry.imo)
        .where(VesselRegistry.imo.isnot(None))
    )).all()
    events = []
    for mmsi, imo, name, source, listed_name, program in rows:
        rule = f"risklist_{source}"
        if await _has_event(session, mmsi, rule, None):
            continue
        events.append(_event(mmsi, rule, imo=imo, name=name,
                             listed_as=listed_name, program=program))
        logger.warning("Risk-list hit: MMSI %s (IMO %s, %s) on %s list [%s]",
                       mmsi, imo, name, source.upper(), program)
    return events


async def compute_scores(session, now: datetime) -> dict[int, float]:
    since = now - timedelta(days=SCORE_WINDOW_DAYS)
    rows = (await session.execute(
        select(RiskEvent.mmsi, func.sum(RiskEvent.score))
        .where(RiskEvent.ts >= since)
        .group_by(RiskEvent.mmsi)
    )).all()
    return {mmsi: float(score) for mmsi, score in rows}


RULE_EXPLANATIONS = {
    "regional_gap": "went dark while underway inside receiver coverage",
    "identity_change": "changed its transmitted identity (name/IMO/callsign)",
    "midsea_appearance": "new identity first seen in open water",
    "impossible_jump": "broadcast physically impossible positions (possible spoofing)",
    "rendezvous": "met another tanker at sea (possible ship-to-ship transfer)",
    "draught_change": "changed draught at sea near an STS zone (possible cargo transfer)",
    "drift_mismatch": "reappeared far from its predicted position",
    "oil_slick": "oil slick detected at its meeting point",
    "dark_association": "anchored among sanctioned ships",
    "loitering": "loitered offshore in a trafficking corridor",
    "mmsi_collision": "one identity broadcasting from two places at once",
    "circle_spoofing": "broadcast a fake circular GPS track",
    "identity_integrity": "fabricated identity (invalid MMSI/IMO)",
    "flag_hop": "reflagged repeatedly (one hull, many MMSIs)",
    "nav_status_lie": "declared anchored while actually moving (often a stale crew setting)",
    "gfw_encounter": "met another vessel at sea (GFW transshipment)",
    "gfw_ais_gap": "disabled AIS (GFW gap)",
    "gfw_loitering": "loitered offshore (GFW)",
}


# how each list describes itself in the "why watched" note - detentions are
# NOT sanctions, so they get their own honest phrasing
LIST_PHRASES = {
    "risklist_ofac": "on the OFAC (US) sanctions list",
    "risklist_gur": "on Ukraine's shadow-fleet list",
    "risklist_eu": "on the EU port-ban list",
    "risklist_uk": "on the UK sanctions list",
    "risklist_uani": "on UANI's Iran tanker list",
    "risklist_canada": "on Canada's sanctions list",
    "risklist_australia": "on Australia's sanctions list",
    "risklist_switzerland": "on Switzerland's sanctions list",
    "risklist_un1718": "on the UN (DPRK) sanctions list",
    "risklist_parismou": "banned from EU ports (Paris MoU)",
    "risklist_iuu": "on the IUU illegal-fishing blacklist",
    "risklist_eu_iuu": "on the EU IUU (illegal-fishing) vessel list",
    "risklist_tokyomou": "detained by port-state control (Tokyo MoU)",
    "risklist_blackseamou": "detained by port-state control (Black Sea MoU)",
    "risklist_abujamou": "detained by port-state control (Abuja MoU)",
}


async def _auto_note(session, mmsi: int) -> str:
    """Human-readable explanation of WHY this ship was auto-added.

    A ship on many lists used to repeat "listed as 'X' but now transmitting as
    'Y'" once per list - a wall of text. We now name each list once and fold the
    name mismatch into a single trailing clause."""
    events = (await session.execute(
        select(RiskEvent).where(RiskEvent.mmsi == mmsi).order_by(RiskEvent.ts.desc())
    )).scalars().all()
    reasons, seen = [], set()
    aliases: dict[str, str] = {}  # upper-cased key -> original casing, deduped
    current_name = None
    has_list = False  # any external-list corroboration (sanctions / PSC detention)
    for e in events:
        if e.rule in seen:
            continue
        seen.add(e.rule)
        details = json.loads(e.details or "{}")
        if e.rule.startswith("risklist_"):
            has_list = True
            reason = LIST_PHRASES.get(e.rule, f"on the {e.rule.removeprefix('risklist_')} list")
            program = details.get("program")
            # skip the internal program tag for detentions (it's just the list name)
            if program and not e.rule.endswith("mou"):
                # some sources concatenate multiple programs with stray brackets
                # ("UKRAINE-EO13662] [RUSSIA-EO14024") - tidy them for display
                program = program.replace("[", "").replace("]", "").strip()
                reason += f" ({program})"
            reasons.append(reason)
            # collect the name it was sanctioned/detained under, once
            name = details.get("name")
            if name:
                current_name = name
            listed = details.get("listed_as")
            # a pure casing difference ('Makalu' vs 'MAKALU') is not a real
            # name change - only flag genuinely different names
            if listed and listed.upper() != (name or "").upper():
                aliases.setdefault(listed.upper(), listed)
        elif e.rule == "identity_change":
            # name the actual change(s), not just the generic label: every
            # identity_change event carries field/old/new in its details
            changes: list[str] = []
            for ev in events:
                if ev.rule != "identity_change":
                    continue
                d = json.loads(ev.details or "{}")
                if d.get("field"):
                    changes.append(f"{d['field']} '{d.get('old') or '?'}' → '{d.get('new') or '?'}'")
            changes = list(dict.fromkeys(changes))  # dedupe, keep order
            if changes:
                shown = ", ".join(changes[:4])
                if len(changes) > 4:
                    shown += f" (+{len(changes) - 4} more)"
                reasons.append(f"changed its transmitted identity: {shown}")
            else:
                reasons.append(RULE_EXPLANATIONS["identity_change"])
        else:
            reasons.append(RULE_EXPLANATIONS.get(e.rule, e.rule))
    # behaviour-only ships (no external list) are heuristic - mark them as
    # unverified so the UI shows they are noise-until-corroborated
    prefix = "[AUTO] " if has_list else "[AUTO - unverified, behaviour only] "
    note = prefix + "; ".join(reasons)
    if aliases and current_name:
        alias_str = "/".join(f"'{a}'" for a in aliases.values())
        note += f"; now broadcasting '{current_name}', previously identified as {alias_str}"
    return note


async def _infer_category(session, mmsi: int, rules: set[str]) -> str:
    """Category = map color. Sanctioned ships are split by what they actually
    are: drug/cartel programs -> narco; PEESA (energy-infrastructure) ->
    sabotage; SDGT (terror-linked) and sanctioned cargo -> smuggling; oil
    tankers -> shadow fleet; fishing lists -> IUU."""
    if "risklist_iuu" in rules or "risklist_eu_iuu" in rules:
        return "iuu_fishing"
    # MoU port-state-control detentions are a SUBSTANDARD-SHIP signal (failed a
    # safety inspection), NOT evidence of illicit trade. A cargo ship detained by
    # PSC is not "smuggling". Only genuine sanctions/ban lists drive the shadow-
    # fleet / smuggling / narco categories; a ship flagged ONLY by detentions is
    # behaviour-flagged ('other') so it doesn't generate false smuggling noise.
    mou_rules = {"risklist_parismou", "risklist_tokyomou",
                 "risklist_blackseamou", "risklist_abujamou"}
    sanction_rules = {r for r in rules if r.startswith("risklist_") and r not in mou_rules}
    if not sanction_rules:
        # Behaviour-only vessel with a GFW signal. We only label it "IUU fishing"
        # when the vessel's OWN AIS ship-type says it is a fishing boat (30-39).
        # We do NOT call it fishing on GFW's classification alone - if we have no
        # ship-type of our own, we cannot honestly claim it is a fishing vessel,
        # so it stays a plain behaviour flag ('other'). The GFW signal (met a
        # vessel / went dark) is still recorded as evidence either way.
        if rules & {"gfw_encounter", "gfw_ais_gap", "gfw_loitering"}:
            our_type = await session.scalar(
                select(VesselRegistry.ship_type).where(VesselRegistry.mmsi == mmsi))
            if our_type is not None and 30 <= our_type <= 39:
                return "iuu_fishing"
        return "other"

    programs = ""
    for e in (await session.execute(
        select(RiskEvent).where(RiskEvent.mmsi == mmsi,
                                RiskEvent.rule.like("risklist_%"))
    )).scalars():
        programs += (json.loads(e.details or "{}").get("program") or "").upper()
    ship_type = await session.scalar(
        select(VesselRegistry.ship_type).where(VesselRegistry.mmsi == mmsi))

    # drug trafficking: Kingpin (SDNTK), EO14059 counter-narcotics, cartels.
    # Venezuela oil sanctions are NOT narco - those stay shadow_fleet (below).
    if any(k in programs for k in ("SDNTK", "ILLICIT-DRUGS", "EO14059", "SOLES", "CARTEL")):
        return "narco"
    if "PEESA" in programs:
        return "sabotage"
    # SHIP TYPE decides first: a tanker is oil (shadow fleet) even under a
    # terror (SDGT) program - it's still moving sanctioned crude, not cargo
    # contraband. Only cargo ships and unknown-type SDGT hulls are smuggling.
    if ship_type is not None and 80 <= ship_type <= 89:
        return "shadow_fleet"           # tanker = sanctioned oil
    if ship_type is not None and 70 <= ship_type <= 79:
        return "smuggling"              # cargo = contraband
    if "SDGT" in programs:
        return "smuggling"              # unknown-type terror-linked hull
    return "shadow_fleet"               # unknown type under an oil program


# The watchlist requires a real ANCHOR: a genuine sanctions/IUU-list match, or a
# hard-to-fake behavioural signal (spoofing / cloned identity / corroborated STS
# draught change). Soft behaviour (gaps, rendezvous, loitering, encounters,
# nav-status lies, mid-sea appearances) and MoU port-state detentions are
# CORROBORATION ONLY - they still surface a vessel as a lead in Suggestions and
# add weight to an already-anchored ship, but they never watchlist a ship alone.
# This keeps the watchlist to vessels you can actually trust are worth watching.
MOU_DETENTION_RULES = {"risklist_parismou", "risklist_tokyomou",
                       "risklist_blackseamou", "risklist_abujamou"}
HARD_RULES = {"identity_change", "mmsi_collision", "circle_spoofing",
              "impossible_jump", "identity_integrity", "draught_change"}


def _qualifies_for_watchlist(rules: set[str], score: float, min_score: float) -> bool:
    if score < min_score:
        return False
    # a genuine sanctions / IUU-list match (NOT a detention - those are the
    # substandard-ship corroboration bucket)
    has_sanction_list = any(
        r.startswith("risklist_") and r not in MOU_DETENTION_RULES for r in rules)
    # a hard-to-fake behavioural signal
    has_hard_signal = bool(rules & HARD_RULES)
    return has_sanction_list or has_hard_signal


async def update_auto_watchlist(session, scores: dict[int, float]) -> None:
    """Populates the shared `vessels` table (the public Suggestions feed).
    The world feed records every ship, so anything scoring past the threshold
    is added as an auto entry; entries that fall below the bar are demoted."""
    s = get_settings()
    if not s.auto_watchlist:
        return

    vessels = (await session.execute(select(Vessel))).scalars().all()
    by_mmsi = {v.mmsi: v for v in vessels}

    # rules per ship (batched) so the behaviour-only qualification gate below can
    # tell a corroborated ship from a lone heuristic without a query per vessel
    relevant = {v.mmsi for v in vessels if v.active and v.auto_added and not v.pinned}
    relevant |= {m for m, sc in scores.items() if sc >= s.suggestion_min_score}
    rules_by: dict[int, set[str]] = {}
    if relevant:
        for m, r in await session.execute(
            select(RiskEvent.mmsi, RiskEvent.rule).where(RiskEvent.mmsi.in_(relevant)).distinct()
        ):
            rules_by.setdefault(m, set()).add(r)

    # keep scores current, and demote auto-added ships that no longer clear the
    # bar (score fell, or a behaviour-only ship lost a corroborating signal) -
    # pinned ships are the user's and are never touched
    for v in vessels:
        if v.mmsi in scores:
            v.risk_score = scores[v.mmsi]
        if v.active and v.auto_added and not v.pinned:
            sc = scores.get(v.mmsi, 0)
            if not _qualifies_for_watchlist(rules_by.get(v.mmsi, set()), sc, s.suggestion_min_score):
                v.active = False
                v.followed = False
                logger.info("Auto-watchlist: demoted MMSI %s (score %.0f, rules %s)",
                            v.mmsi, sc, sorted(rules_by.get(v.mmsi, set())))

    candidates = sorted(
        ((m, sc) for m, sc in scores.items()
         if _qualifies_for_watchlist(rules_by.get(m, set()), sc, s.suggestion_min_score)
         and not (by_mmsi.get(m) and by_mmsi[m].active)),
        key=lambda x: -x[1],
    )
    for mmsi, score in candidates:
        existing = by_mmsi.get(mmsi)
        if existing and existing.pinned:
            continue  # user removed/controls this ship - the engine stays out
        reg = await session.get(VesselRegistry, mmsi)
        rules = {row[0] for row in await session.execute(
            select(RiskEvent.rule).where(RiskEvent.mmsi == mmsi).distinct())}
        if existing:
            existing.active = True
            existing.risk_score = score
            existing.notes = await _auto_note(session, mmsi)
            existing.category = await _infer_category(session, mmsi, rules)
        else:
            vessel = Vessel(
                mmsi=mmsi, imo=(reg.imo if reg else None),
                name=(reg.name if reg else None),
                category=await _infer_category(session, mmsi, rules),
                notes=await _auto_note(session, mmsi),
                pinned=False, auto_added=True, risk_score=score,
            )
            session.add(vessel)
            by_mmsi[mmsi] = vessel
        logger.info("Auto-watchlist: added MMSI %s (score %.0f, rules: %s)",
                    mmsi, score, ", ".join(sorted(rules)))


async def run_behavior_scan() -> None:
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        events: list[RiskEvent] = []
        events += await rule_regional_gaps(session, now)
        events += await rule_identity_changes(session)
        events += await rule_midsea_appearance(session, now)
        events += await rule_impossible_jumps(session, now)
        events += await rule_rendezvous(session, now)
        events += await rule_oil_slick_corroboration(session, now)
        events += await rule_draught_change(session, now)
        events += await rule_dark_association(session, now)
        events += await rule_loitering(session, now)
        events += await rule_mmsi_collision(session, now)
        events += await rule_circle_spoofing(session, now)
        events += await rule_identity_integrity(session)
        events += await rule_flag_hopping(session)
        events += await rule_nav_status_lie(session, now)
        events += await rule_risklist_matches(session)
        session.add_all(events)
        await session.flush()

        scores = await compute_scores(session, now)
        await update_auto_watchlist(session, scores)
        await prune_heartbeats(session)
        await session.commit()

    if events:
        logger.info("Behaviour scan: %d new events (%s)", len(events),
                    ", ".join(sorted({e.rule for e in events})))
