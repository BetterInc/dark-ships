"""Seed the watchlist with documented example vessels.

Run:  python -m app.seed              (local)
      docker compose exec backend python -m app.seed

NOTE: MMSIs change when a ship reflags - illicit fleets do this constantly,
which is exactly why only the IMO number is a stable identifier. Look up the
current MMSI by IMO on marinetraffic.com or vesselfinder.com and fill it in
below. Entries with mmsi=0 are skipped.
"""

import asyncio

from sqlalchemy import select

from .db import SessionLocal
from .models import Vessel

# MMSIs verified July 2026 via MarineTraffic/VesselFinder/vesseltracker.
# MMSIs change on reflagging - if a ship stops appearing, re-check by IMO.
# Only ships still sailing belong here; detained/seized ones (Eagle S,
# Arconian) transmit nothing worth watching.
SEED_VESSELS = [
    {
        "mmsi": 414_270_000,
        "imo": "9224984",
        "name": "YI PENG 3",
        "flag": "China",
        "category": "sabotage",
        "notes": "Bulk carrier that dragged its port anchor ~180 nm across the Baltic, "
                 "cutting the C-Lion1 (Finland-Germany) and BCS East-West data cables "
                 "(Nov 2024) after leaving a Russian port. Active again (Shanghai, Jun 2026).",
    },
    # Named suspects caught in the act (2025-2026), not yet on the sanctions
    # lists we ingest. Verify current MMSI by IMO on vesselfinder.com.
    {
        "mmsi": 375_068_000,
        "imo": "9250397",
        "name": "FITBURG",
        "flag": "St Vincent & Grenadines",
        "category": "sabotage",
        "notes": "Cargo ship detained by Finland Dec 2025: dragged anchor and severed the "
                 "Elisa/Arelion cables in the Gulf of Finland; carried sanctioned Russian "
                 "steel; 4 crew charged. Suspected hybrid attack.",
    },
    {
        "mmsi": 0,  # 6+ rotating MMSIs; look up current one by IMO
        "imo": "8358427",
        "name": "XINGSHUN 39",
        "flag": "Cameroon / Tanzania (rotating)",
        "category": "sabotage",
        "notes": "'Ghost ship' that severed a Taiwan cable off Keelung (Jan 2025). Uses 3 "
                 "identities and 6+ MMSIs, spoofed another ship's IMO. Still active - the "
                 "textbook identity-laundering cable-cutter.",
    },
]


async def seed() -> None:
    async with SessionLocal() as session:
        for entry in SEED_VESSELS:
            if not entry["mmsi"]:
                print(f"SKIP {entry['name']}: no MMSI filled in (IMO {entry['imo']}) - "
                      f"look it up via https://www.vesselfinder.com/vessels?name={entry['imo']}")
                continue
            exists = await session.scalar(select(Vessel).where(Vessel.mmsi == entry["mmsi"]))
            if exists:
                print(f"OK   {entry['name']}: already on the watchlist")
                continue
            session.add(Vessel(**entry))
            print(f"ADD  {entry['name']} (MMSI {entry['mmsi']})")
        await session.commit()


if __name__ == "__main__":
    asyncio.run(seed())
