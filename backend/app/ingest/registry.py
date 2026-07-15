"""Vessel registry tracker.

Builds our own MMSI <-> IMO <-> name bridge from the feed itself:
- position reports register first/last sighting (with first position)
- ShipStaticData messages carry IMO + name + callsign; any change to those
  fields is recorded as an identity_change (the Arconian pattern: new name,
  MMSI and flag during an AIS blackout)
"""

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..db import SessionLocal
from ..models import DraughtChange, IdentityChange, VesselRegistry

logger = logging.getLogger(__name__)

FLUSH_INTERVAL_SECONDS = 5.0
LAST_SEEN_UPDATE_MINUTES = 10  # don't rewrite last_seen on every message

IDENTITY_FIELDS = ("name", "imo", "callsign")

# Leading vessel-type token that is not part of the actual name: MV, MT, M/V,
# M.T., MS, SS, FV, MSV. Dropping "MV " is a formatting difference, not a rename.
_NAME_PREFIX_RE = re.compile(r"^(M[./]?[VT]|MS|SS|FV|MSV)\b[\s./-]*", re.IGNORECASE)


def _normalize_name(n: str) -> str:
    """Canonical form for comparing vessel names: drop a leading MV/MT-style
    prefix, then strip case, spaces and punctuation."""
    s = _NAME_PREFIX_RE.sub("", (n or "").strip())
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a or not b:
        return len(a) or len(b)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _significant_change(field: str, old: str, new: str) -> bool:
    """A real identity change, not a formatting/typo variant. IMO and callsign
    are compared exactly; names ignore MV/MT prefixes, punctuation and a
    one-character typo/OCR drift (e.g. MARBELLO -> MARBELO is NOT a rename)."""
    if field != "name":
        return True
    a, b = _normalize_name(old), _normalize_name(new)
    if a == b:
        return False
    return _levenshtein(a, b) > max(1, min(len(a), len(b)) // 6)

# MaximumStaticDraught (metres) sanity band. The feed carries junk (0.0-0.8
# unset defaults, and occasional garbage) - only draughts in this range are
# plausible for a sea-going ship and may seed/trigger a DraughtChange.
DRAUGHT_MIN_M, DRAUGHT_MAX_M = 1.0, 25.0
# A laden<->ballast swing is metres, not a trim adjustment. Recorded here at a
# slightly looser bar than the behaviour rule's authoritative DRAUGHT_STS_DELTA
# so the rule stays the strict gate.
DRAUGHT_SIGNIFICANT_DELTA_M = 2.0


def valid_draught(d: float | None) -> bool:
    return d is not None and DRAUGHT_MIN_M <= d <= DRAUGHT_MAX_M


def valid_imo(imo: str) -> bool:
    """IMO numbers carry a check digit: sum(d1..d6 × 7..2) mod 10 == d7.
    Transmission/transcription glitches (audit case: a government survey ship
    broadcasting 9031369 instead of its registered 9031387) fail this check
    and must not enter the registry or fire identity events."""
    if not imo or len(imo) != 7 or not imo.isdigit():
        return False
    return sum(int(d) * w for d, w in zip(imo[:6], range(7, 1, -1))) % 10 == int(imo[6])


class RegistryTracker:
    def __init__(self):
        # mmsi -> {"name":..., "imo":..., "callsign":..., "last_seen_write": dt, "known": bool}
        self._cache: dict[int, dict] = {}
        self._upserts: dict[int, dict] = {}
        self._changes: list[dict] = []
        self._draught_changes: list[dict] = []
        self._lock = asyncio.Lock()

    async def observe_position(self, mmsi: int, ts: datetime, lat: float, lon: float,
                               ship_name: str | None) -> None:
        async with self._lock:
            entry = self._cache.get(mmsi)
            if entry is None:
                self._cache[mmsi] = {"last_seen_write": ts, "known": False}
                self._upserts[mmsi] = {
                    "mmsi": mmsi, "name": ship_name, "first_seen": ts,
                    "first_lat": lat, "first_lon": lon, "last_seen": ts,
                }
            elif ts - entry["last_seen_write"] > timedelta(minutes=LAST_SEEN_UPDATE_MINUTES):
                entry["last_seen_write"] = ts
                self._upserts.setdefault(mmsi, {"mmsi": mmsi})["last_seen"] = ts

    async def observe_static(self, mmsi: int, ts: datetime, name: str | None,
                             imo: str | None, callsign: str | None,
                             ship_type: int | None, destination: str | None,
                             draught: float | None = None,
                             length_m: float | None = None,
                             beam_m: float | None = None) -> None:
        fresh = {"name": name, "imo": imo, "callsign": callsign}
        async with self._lock:
            entry = self._cache.get(mmsi)
            if entry is None:
                entry = {"last_seen_write": ts, "known": False}
                self._cache[mmsi] = entry

        if not entry.get("static_loaded"):
            # first static message for this MMSI this session: load DB state once
            async with SessionLocal() as session:
                row = await session.get(VesselRegistry, mmsi)
            async with self._lock:
                entry["static_loaded"] = True
                # Identity comparison must be static-vs-static: a registry name
                # that came from position metadata (AISstream's cache) is a
                # different SOURCE, and source disagreement is not an identity
                # change by the ship. Only seed 'name' if a static message
                # wrote it before; imo/callsign only ever come from statics.
                from_static = row is not None and row.static_updated_at is not None
                for field in IDENTITY_FIELDS:
                    if field == "name" and not from_static:
                        continue
                    entry.setdefault(field, getattr(row, field, None) if row else None)
                # seed the last known valid draught so a change survives restarts
                entry.setdefault("draught", getattr(row, "draught", None) if row else None)

        async with self._lock:
            pending = entry.setdefault("pending", {})
            for field in IDENTITY_FIELDS:
                old = entry.get(field)
                new = fresh[field]
                # A textual difference that is a real change, not a formatting
                # variant (dropped "MV " prefix) or a one-character typo.
                differs = bool(old and new and old != new
                               and _significant_change(field, old, new))
                if differs:
                    # Debounce: AIS static payloads arrive corrupted often
                    # enough (audit case: "PERGAMON SEAWAYS" -> garbage chars)
                    # that a change only counts when the SAME new value shows
                    # up twice in a row. Random bit errors don't repeat;
                    # genuine renames do, on every subsequent broadcast.
                    if pending.get(field) != new:
                        pending[field] = new
                        continue
                    del pending[field]
                    self._changes.append({
                        "mmsi": mmsi, "ts": ts, "field": field,
                        "old_value": str(old)[:128], "new_value": str(new)[:128],
                    })
                    logger.warning("Identity change MMSI %s: %s '%s' -> '%s' (confirmed twice)",
                                   mmsi, field, old, new)
                elif field in pending:
                    del pending[field]  # value reverted - was a one-off glitch
                if differs and field in pending:
                    continue  # not confirmed yet: keep the old identity
                # adopt the new value (including harmless spelling/prefix drift)
                if new:
                    entry[field] = new

            # Draught: only valid draughts count. A significant swing from the
            # previously stored valid draught is the STS cargo-transfer signal;
            # record it (the behaviour rule resolves location + guardrails).
            if valid_draught(draught):
                old_draught = entry.get("draught")
                if valid_draught(old_draught) and \
                        abs(draught - old_draught) >= DRAUGHT_SIGNIFICANT_DELTA_M:
                    self._draught_changes.append({
                        "mmsi": mmsi, "ts": datetime.now(timezone.utc),
                        "old_draught": old_draught, "new_draught": draught,
                    })
                    logger.info("Draught change MMSI %s: %.1f -> %.1f m",
                                mmsi, old_draught, draught)
                entry["draught"] = draught

            # ship_type/destination: only overwrite when this message actually
            # carries them. A Class B StaticDataReport alternates part A (name,
            # NO ShipType) and part B (ShipType); writing the None from part A
            # would flap a known ship_type to NULL every cycle and silently
            # break every type-gated rule. Persist in `entry` and only include
            # the key in the upsert when we have a value (same as draught).
            if ship_type is not None:
                entry["ship_type"] = ship_type
            dest = (destination or "").strip() or None
            if dest is not None:
                entry["destination"] = dest
            # AIS dimensions: plausibility-gated like draught (unset defaults
            # and garbage are common); only overwrite when the message carries
            # a sane value, so a flapping zero can't erase a known size.
            if length_m is not None and 5.0 <= length_m <= 470.0:
                entry["length_m"] = length_m
            if beam_m is not None and 1.5 <= beam_m <= 80.0:
                entry["beam_m"] = beam_m

            up = self._upserts.setdefault(mmsi, {"mmsi": mmsi})
            up.update({
                "name": entry.get("name"), "imo": entry.get("imo"),
                "callsign": entry.get("callsign"),
                "last_seen": ts, "static_updated_at": ts,
            })
            if entry.get("ship_type") is not None:
                up["ship_type"] = entry["ship_type"]
            if entry.get("destination") is not None:
                up["destination"] = entry["destination"]
            if valid_draught(entry.get("draught")):
                up["draught"] = entry["draught"]
            if entry.get("length_m"):
                up["length_m"] = entry["length_m"]
            if entry.get("beam_m"):
                up["beam_m"] = entry["beam_m"]
            up.setdefault("first_seen", ts)

    async def flush_forever(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_INTERVAL_SECONDS)
            async with self._lock:
                upserts, self._upserts = self._upserts, {}
                changes, self._changes = self._changes, []
                draught_changes, self._draught_changes = self._draught_changes, []
            if not upserts and not changes and not draught_changes:
                continue
            try:
                async with SessionLocal() as session:
                    for row in upserts.values():
                        row.setdefault("first_seen", row.get("last_seen", datetime.now(timezone.utc)))
                        row.setdefault("last_seen", row["first_seen"])
                        stmt = pg_insert(VesselRegistry).values(**row)
                        update_cols = {k: stmt.excluded[k] for k in row
                                       if k not in ("mmsi", "first_seen", "first_lat", "first_lon")}
                        stmt = stmt.on_conflict_do_update(index_elements=["mmsi"], set_=update_cols)
                        await session.execute(stmt)
                    for change in changes:
                        session.add(IdentityChange(**change))
                    for dc in draught_changes:
                        session.add(DraughtChange(**dc))
                    await session.commit()
            except Exception:
                logger.exception("Registry flush failed (%d upserts, %d changes, %d draught)",
                                 len(upserts), len(changes), len(draught_changes))

    async def preload_known(self) -> None:
        """Warm the cache with MMSIs already in the registry, so 'new vessel'
        detection survives restarts."""
        async with SessionLocal() as session:
            result = await session.execute(select(VesselRegistry.mmsi))
            mmsis = [row[0] for row in result]
        now = datetime.now(timezone.utc)
        async with self._lock:
            for mmsi in mmsis:
                self._cache.setdefault(mmsi, {"last_seen_write": now, "known": True})
        logger.info("Registry cache preloaded with %d known vessels", len(mmsis))
