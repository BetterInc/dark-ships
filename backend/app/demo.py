"""Populate the map and events feed with DEMO vessels - one per threat category.

Run:  docker compose exec backend python -m app.demo

Each demo vessel gets a short, realistic track at a documented hotspot for its
category, ending 26 hours ago while underway. The real gap detector then opens
an AIS-gap event for each, and the real Copernicus matcher attaches actual
Sentinel-1/2 scenes from that window. Demo MMSIs start with 999 (an unassigned
MID), so they can never collide with live AIS traffic.

Re-running the script wipes and recreates the demo data.
Remove it for good:  docker compose exec backend python -m app.demo --clean
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from .db import SessionLocal
from .jobs.gap_detector import run_gap_detection
from .models import AisGap, Position, SarMatch, Vessel

DEMO_MMSI_MIN, DEMO_MMSI_MAX = 999_000_000, 999_000_999

# (mmsi, name, flag, category, notes, start lat/lon, course deg, speed kn) -
# hotspots taken from documented cases per category
DEMO_VESSELS = [
    (999_000_001, "DEMO BALTIC HAULER", "Gabon", "shadow_fleet",
     "[DEMO] Laden crude tanker out of Primorsk, AIS silent east of Gotland - "
     "classic shadow-fleet pattern in the Baltic corridor.",
     57.2, 19.8, 225.0, 11.0),
    (999_000_002, "DEMO ATLANTIC RUNNER", "Tanzania", "narco",
     "[DEMO] Freetown departure, 'Libya' as declared destination, went dark on "
     "the Canary route - the Arconian pattern.",
     23.7, -16.2, 30.0, 12.5),
    (999_000_003, "DEMO SQUID CHASER", "Unknown", "iuu_fishing",
     "[DEMO] Trawler dark at the edge of the Argentine EEZ squid grounds - "
     "the classic IUU night-fishing blackout.",
     -45.8, -60.2, 90.0, 6.5),
    (999_000_004, "DEMO ANCHOR DRAGGER", "China", "sabotage",
     "[DEMO] Slow transit over the C-Lion1 cable corridor in the Gulf of "
     "Finland, then silent - the Eagle S / Yi Peng 3 pattern.",
     59.7, 25.2, 270.0, 7.0),
    (999_000_005, "DEMO GULF PHANTOM", "Cameroon", "smuggling",
     "[DEMO] Coaster gone dark in the Gulf of Guinea bunkering zone - "
     "typical for fuel/arms smuggling runs.",
     3.4, 6.2, 180.0, 9.0),
]

TRACK_HOURS = 8          # length of the visible track before the blackout
GAP_AGE_HOURS = 26       # how long ago the last position was sent


async def clean(session) -> list[int]:
    mmsis = [v[0] for v in DEMO_VESSELS]
    gap_ids = [row[0] for row in await session.execute(
        select(AisGap.id).where(AisGap.mmsi.in_(mmsis)))]
    if gap_ids:
        await session.execute(delete(SarMatch).where(SarMatch.gap_id.in_(gap_ids)))
    await session.execute(delete(AisGap).where(AisGap.mmsi.in_(mmsis)))
    await session.execute(delete(Position).where(Position.mmsi.in_(mmsis)))
    await session.execute(delete(Vessel).where(Vessel.mmsi.in_(mmsis)))
    # also drop the old end-to-end test vessel if it is still around
    await session.execute(delete(AisGap).where(AisGap.mmsi == 244_000_001))
    await session.execute(delete(Position).where(Position.mmsi == 244_000_001))
    await session.execute(delete(Vessel).where(Vessel.mmsi == 244_000_001))
    return mmsis


async def main() -> None:
    wipe_only = "--clean" in sys.argv
    now = datetime.now(timezone.utc)

    async with SessionLocal() as session:
        await clean(session)
        if wipe_only:
            await session.commit()
            print("Demo data removed.")
            return

        for mmsi, name, flag, category, notes, lat, lon, course, sog in DEMO_VESSELS:
            session.add(Vessel(mmsi=mmsi, name=name, flag=flag,
                               category=category, notes=notes))
            # straight-line track ending GAP_AGE_HOURS ago
            import math
            for step in range(TRACK_HOURS, -1, -1):
                ts = now - timedelta(hours=GAP_AGE_HOURS + step)
                dist_nm = sog * (TRACK_HOURS - step)
                dlat = dist_nm / 60.0 * math.cos(math.radians(course))
                dlon = dist_nm / (60.0 * max(math.cos(math.radians(lat)), 0.1)) * math.sin(math.radians(course))
                session.add(Position(
                    mmsi=mmsi, ts=ts, lat=lat + dlat, lon=lon + dlon,
                    sog=sog, cog=course, heading=course, nav_status=0,
                    ship_name=name, source="watchlist",
                ))
            print(f"ADD  {name} ({category}) - last position {GAP_AGE_HOURS}h ago")
        await session.commit()

    print("Running gap detection + Sentinel matching (queries the live Copernicus catalogue)...")
    await run_gap_detection()
    print("Done - check the map and the events feed.")


if __name__ == "__main__":
    asyncio.run(main())
