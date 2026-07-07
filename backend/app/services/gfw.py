"""Global Fishing Watch (GFW) events - behavioural IUU-fishing signals.

We pull three GFW event datasets over our configured regions and turn each into
a scored RiskEvent so NEW illegal-fishing activity (ship-to-ship transshipment,
AIS disabling, offshore loitering) flows into the same behaviour engine and
Suggestions feed as our own detectors - not just matches against known lists.

DATA LICENCE: GFW event data is CC BY-NC 4.0 (NON-COMMERCIAL). It is fine while
this product is free; a commercial deployment needs a GFW commercial licence.
Attribution: Global Fishing Watch, globalfishingwatch.org.

API: v3 events. `POST /v3/events?limit=&offset=` (both query params REQUIRED),
JSON body {datasets, startDate, endDate, geometry:<GeoJSON Polygon>}. Response
{"total": int, "entries": [...]}. We link a GFW event to our data through the
primary vessel's ssvid, which equals the MMSI.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from ..config import get_settings
from ..db import SessionLocal
from ..jobs.behavior import _event
from ..models import Position, RiskEvent, RiskListEntry, VesselRegistry

logger = logging.getLogger(__name__)

BASE_URL = "https://gateway.api.globalfishingwatch.org/v3/events"

# dataset -> (rule, event kind). One dataset per request keeps the rule mapping
# unambiguous instead of inferring it from each entry's `type`.
DATASETS = {
    "public-global-encounters-events:latest": "gfw_encounter",
    "public-global-gaps-events:latest": "gfw_ais_gap",
    "public-global-loitering-events:latest": "gfw_loitering",
}

GFW_RULES = set(DATASETS.values())

LOOKBACK_DAYS = 30
PAGE_LIMIT = 100          # entries per request
MAX_PAGES_PER_QUERY = 10  # hard cap: at most 1000 entries scanned per query
HTTP_TIMEOUT = 90

# --- flooding guardrails (a busy region can hold hundreds of events) ---
MAX_NEW_EVENTS_PER_RUN = 450          # stop importing once we hit this
# Per-rule caps, weighted by value. Transshipment encounters are the signal we
# most want; AIS gaps (disabling) are next; loitering is everywhere and lowest
# value, so it gets a hard low cap and can never flood out the rest.
PER_RULE_CAP = {
    "gfw_encounter": 250,
    "gfw_ais_gap": 150,
    "gfw_loitering": 50,
}
ENCOUNTER_MAX_KM = 1.0                # a real STS/meeting is close alongside
# Real transshipment = a fishing/other hull offloading to a CARRIER/REEFER at
# sea (to launder catch, skip port inspection). Two fishing boats meeting in a
# regulated fishery is normal fleet behaviour, so require a carrier/reefer on at
# least one side rather than flagging every fishing-to-fishing encounter.
CARRIER_TYPES = {"carrier", "reefer", "specialized_reefer", "fish_factory"}
GAP_MIN_HOURS = 3.0                   # ignore brief AIS blips
# Upper bound: a "gap" of many weeks is a laid-up / decommissioned / left-the-
# dataset vessel, not a vessel switching off AIS to hide a movement. Past ~30
# days it is a data artifact (audit case: a 663-day "gap"), so cap it.
GAP_MAX_HOURS = 720.0                 # 30 days
LOITER_MIN_HOURS = 3.0                # sustained dwell only
# A "loiter" spanning many days is a laid-up / stored vessel sitting at anchor,
# not a behavioural signal, and those flood busy anchorages - skip them. (No
# upper bound on gaps: a longer dark period is MORE suspicious, not less.)
LOITER_MAX_HOURS = 336.0              # 14 days

# GFW `vessel.type` values that make a behaviour signal an IUU-fishing signal
# (rather than the generic behaviour flag) - consumed by _infer_category.
FISHING_TYPES = {"fishing", "carrier", "reefer", "fish_factory", "specialized_reefer"}


def _polygon(bbox: list[list[float]]) -> dict:
    """Region bbox [[lat_min,lon_min],[lat_max,lon_max]] -> GeoJSON Polygon.
    GeoJSON coordinates are [lon, lat]."""
    (lat_min, lon_min), (lat_max, lon_max) = bbox
    return {
        "type": "Polygon",
        "coordinates": [[
            [lon_min, lat_min],
            [lon_max, lat_min],
            [lon_max, lat_max],
            [lon_min, lat_max],
            [lon_min, lat_min],
        ]],
    }


def _parse_dt(raw) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _duration_hours(entry: dict) -> float | None:
    start, end = _parse_dt(entry.get("start")), _parse_dt(entry.get("end"))
    if start is None or end is None:
        return None
    return (end - start).total_seconds() / 3600


def _mmsi(vessel: dict | None) -> int | None:
    if not vessel:
        return None
    ssvid = vessel.get("ssvid")
    if ssvid is None:
        return None
    ssvid = str(ssvid).strip()
    return int(ssvid) if ssvid.isdigit() and len(ssvid) == 9 else None


def _is_station_keeper(ship_type: int | None) -> bool:
    """AIS ship types whose job is to hold position: tugs/pilot/SAR/survey (50-59)
    and other/survey (90-99). Loitering by these is normal, not a signal."""
    return ship_type is not None and (50 <= ship_type <= 59 or 90 <= ship_type <= 99)


async def _relevant_scope() -> tuple[set[int], dict[int, int]]:
    """Vessels worth importing GFW events for: ones we can locate (have logged a
    position) or that are on a risk list. GFW runs on global satellite AIS while
    we cover terrestrial-range coastal water, so most GFW vessels are outside our
    coverage - importing those is noise we can't act on. "Locatable" = we logged
    a real position in the last LOOKBACK_DAYS (matches GFW's window and only
    scans recent partitions). Also returns mmsi -> AIS ship_type for the
    loitering station-keeper filter."""
    since = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    async with SessionLocal() as session:
        located = {m for (m,) in await session.execute(
            select(Position.mmsi).where(Position.ts >= since).distinct())}
        listed = {m for (m,) in await session.execute(
            select(VesselRegistry.mmsi)
            .join(RiskListEntry, RiskListEntry.imo == VesselRegistry.imo))}
        types = {m: t for m, t in await session.execute(
            select(VesselRegistry.mmsi, VesselRegistry.ship_type))}
    return located | listed, types


async def _load_imported_ids() -> set[str]:
    """GFW event ids we already turned into RiskEvents (last ~40 days), so a
    re-run never imports the same event twice."""
    since = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS + 10)
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(RiskEvent.details).where(
                RiskEvent.rule.in_(GFW_RULES), RiskEvent.ts >= since)
        )).scalars().all()
    ids: set[str] = set()
    for details in rows:
        try:
            gid = json.loads(details or "{}").get("gfw_id")
        except (json.JSONDecodeError, TypeError):
            continue
        if gid:
            ids.add(gid)
    return ids


async def _fetch_events(client: httpx.AsyncClient, dataset: str,
                        polygon: dict, start: str, end: str) -> list[dict]:
    """Paginate one dataset over one region, capped at MAX_PAGES_PER_QUERY."""
    body = {"datasets": [dataset], "startDate": start, "endDate": end,
            "geometry": polygon}
    entries: list[dict] = []
    offset = 0
    for _ in range(MAX_PAGES_PER_QUERY):
        resp = await client.post(
            BASE_URL, params={"limit": PAGE_LIMIT, "offset": offset}, json=body)
        resp.raise_for_status()
        payload = resp.json()
        page = payload.get("entries", [])
        entries.extend(page)
        total = payload.get("total", 0)
        offset += PAGE_LIMIT
        if len(page) < PAGE_LIMIT or offset >= total:
            break
    return entries


def _event_from_entry(rule: str, entry: dict) -> RiskEvent | None:
    """Turn a GFW entry into a RiskEvent, applying the per-type guardrails.
    Returns None if the entry is uninteresting or unlinkable."""
    vessel = entry.get("vessel") or {}
    mmsi = _mmsi(vessel)
    if mmsi is None:
        return None
    gfw_id = entry.get("id")
    if not gfw_id:
        return None
    pos = entry.get("position") or {}
    lat, lon = pos.get("lat"), pos.get("lon")
    vtype = (vessel.get("type") or "").lower()
    common = dict(gfw_id=gfw_id, lat=lat, lon=lon, vessel_type=vtype,
                  vessel_name=vessel.get("name"), vessel_flag=vessel.get("flag"))

    if rule == "gfw_encounter":
        enc = entry.get("encounter") or {}
        other = enc.get("vessel") or {}
        otype = (other.get("type") or "").lower()
        # keep only meetings that look like transshipment: a carrier/reefer on at
        # least one side (the receiving hull), and the two vessels close alongside
        if not ({vtype, otype} & CARRIER_TYPES):
            return None
        dist = enc.get("medianDistanceKilometers")
        if dist is not None and dist > ENCOUNTER_MAX_KM:
            return None
        return _event(mmsi, rule, other_ssvid=other.get("ssvid"),
                      other_name=other.get("name"), other_flag=other.get("flag"),
                      other_type=otype, distance_km=dist, **common)

    if rule == "gfw_ais_gap":
        hours = _duration_hours(entry)
        if hours is None or not (GAP_MIN_HOURS <= hours <= GAP_MAX_HOURS):
            return None
        return _event(mmsi, rule, gap_hours=round(hours, 1), **common)

    if rule == "gfw_loitering":
        hours = _duration_hours(entry)
        if hours is None or not (LOITER_MIN_HOURS <= hours <= LOITER_MAX_HOURS):
            return None
        return _event(mmsi, rule, loiter_hours=round(hours, 1), **common)

    return None


async def sync_gfw_events() -> int:
    """Import recent GFW encounter/gap/loitering events for every configured
    region as scored RiskEvents. Deduplicated by GFW event id; one failing
    region or dataset is logged and skipped so it never aborts the whole run.
    Returns the number of new RiskEvents created."""
    settings = get_settings()
    if not settings.gfw_api_token:
        logger.warning("GFW sync skipped: GFW_API_TOKEN not set")
        return 0
    if not settings.ais_regions:
        return 0

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)
    start_s, end_s = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    seen_ids = await _load_imported_ids()
    relevant, ship_types = await _relevant_scope()
    headers = {"Authorization": f"Bearer {settings.gfw_api_token}"}
    new_events: list[RiskEvent] = []
    per_rule: dict[str, int] = {r: 0 for r in GFW_RULES}
    capped = False

    # Dataset-outer, region-inner: sweep the rarer, higher-value signals
    # (transshipment encounters) across every region before the common ones, so
    # a flood of loitering in one busy anchorage cannot starve the rest.
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers=headers) as client:
        for dataset, rule in DATASETS.items():
            if capped:
                break
            rule_cap = PER_RULE_CAP.get(rule, 100)
            for region in settings.ais_regions:
                if capped or per_rule[rule] >= rule_cap:
                    break  # this rule is full; move to the next dataset
                polygon = _polygon(region.bbox)
                try:
                    entries = await _fetch_events(client, dataset, polygon, start_s, end_s)
                except Exception:
                    logger.exception("GFW %s fetch failed for %s", dataset, region.name)
                    continue
                for entry in entries:
                    gfw_id = entry.get("id")
                    if not gfw_id or gfw_id in seen_ids:
                        continue
                    event = _event_from_entry(rule, entry)
                    if event is None:
                        continue
                    # import scoping: skip vessels we can't locate and that are
                    # not on any list - GFW's global feed is mostly outside our
                    # coverage, and those events are noise we can't act on
                    if event.mmsi not in relevant:
                        continue
                    # loitering by a station-keeping hull (tug/survey/SAR) is its job
                    if rule == "gfw_loitering" and _is_station_keeper(ship_types.get(event.mmsi)):
                        continue
                    seen_ids.add(gfw_id)  # guard against dupes within this run too
                    new_events.append(event)
                    per_rule[rule] += 1
                    if len(new_events) >= MAX_NEW_EVENTS_PER_RUN:
                        capped = True
                        break
                    if per_rule[rule] >= rule_cap:
                        logger.info("GFW %s hit per-rule cap of %d", rule, rule_cap)
                        break

    if capped:
        logger.warning("GFW sync hit the %d-event cap - some events deferred to next run",
                       MAX_NEW_EVENTS_PER_RUN)

    if new_events:
        # Re-check against ids committed by any concurrent run since we started
        # (a dev auto-reload or an overlapping schedule can run two syncs at
        # once). Without this they would both insert the same events and double
        # a vessel's score. Not a perfect lock, but closes the common window.
        committed = await _load_imported_ids()
        new_events = [e for e in new_events
                      if json.loads(e.details).get("gfw_id") not in committed]
    if new_events:
        async with SessionLocal() as session:
            session.add_all(new_events)
            await session.commit()
        logger.info("GFW sync: %d new events", len(new_events))
    else:
        logger.info("GFW sync: no new events")
    return len(new_events)
