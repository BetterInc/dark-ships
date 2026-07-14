# Dark Ships

Maritime-intelligence platform that detects suspicious vessel behaviour using
**free data sources only**. It ingests live AIS worldwide, scores vessels
against sanction/detention/fishing lists and behavioural rules, cross-checks
Global Fishing Watch and satellite imagery, and lets each user keep a private
watchlist and live event feed.

Stack: Postgres 16 + FastAPI (async) backend, React + Vite + TypeScript +
MapLibre frontend, all via `docker compose`.

## What it does

- **Live AIS worldwide** via [AISstream.io](https://aisstream.io). One
  connection subscribed to a global bounding box; every ship is stored so any
  vessel can be located and its track replayed after the fact. Only real
  received positions are ever shown (no dead-reckoning or extrapolation).
- **Behaviour engine** scores vessels every 10 minutes on ~18 rules (going
  dark, identity/flag changes, spoofing, ship-to-ship, draught changes, and
  more) plus government and NGO risk lists.
- **Risk lists** imported daily: OFAC, UK, EU, Ukraine GUR, Canada, Australia,
  Switzerland, UN 1718 (DPRK), UANI, Paris/Tokyo/Black Sea/Abuja MoU
  detentions, RFMO IUU fishing blacklist and the EU IUU list.
- **Global Fishing Watch** behavioural events (encounters, AIS gaps,
  loitering) folded into the same engine.
- **Satellite verification**: for each AIS gap, Sentinel-1 (SAR) and
  Sentinel-2 (optical) acquisitions intersecting the drift area are attached,
  with quicklook and Copernicus Browser links.
- **Accounts**: registration, login, password reset, and optional Google
  sign-in. Each user keeps a **private watchlist** and a **live event feed**
  for the ships they follow.

## Threat categories

| Category | What it is | Documented examples |
|---|---|---|
| `shadow_fleet` | Sanctions-evading oil transport (Russia, Iran, Venezuela, North Korea); ~1,400 tankers under rotating flags and shell owners | Eagle S (IMO 9329760) |
| `narco` | Drug trafficking, often mother-ships doing offshore handovers to fast boats | Arconian (IMO 8988882, ~1.5 t cocaine, Nov 2025) |
| `iuu_fishing` | Illegal, unreported and unregulated fishing; AIS blackout on fishing grounds | STS-50 / Andrey Dolgov (~$50M toothfish over a decade) |
| `sabotage` | Infrastructure sabotage: anchor-dragging over cables/pipelines, spy ships | Eagle S (EstLink 2), Yi Peng 3 (IMO 9224984, C-Lion1) |
| `smuggling` | Arms, contraband, waste dumping; overlaps heavily with the shadow fleet | Russian dark-fleet arms transports (FTM investigation) |
| `other` | Anything else worth watching | - |

## Accounts and access

| Area | Access |
|---|---|
| Map | Public |
| Sources | Public (view the lists; no downloads) |
| My watchlist | Login required, private per user |
| Suggestions | Login required |
| Events | Login required |
| Imagery | Login required |

The free tier follows up to **10 vessels** (`FREE_FOLLOW_LIMIT`). Paid monthly
plans (100 / 1000 / 10k) are planned. Recording of positions is done by the
system worker, so following a ship never opens a dedicated provider connection.

## Setup

1. Create a free API key at https://aisstream.io (GitHub login).
2. `cp .env.example .env` and fill in `AISSTREAM_API_KEY_REGIONS`. Optionally
   set `GFW_API_TOKEN` (Global Fishing Watch), `AUTH_SECRET` (sign JWTs and
   reset tokens; change in production), and Google OAuth creds. Tune
   `AIS_REGIONS`, `GAP_THRESHOLD_HOURS`, throttles, etc. as needed.
3. Start the stack:

   ```sh
   docker compose up --build
   ```

   - Frontend: http://localhost:5173
   - API docs: http://localhost:8000/docs
   - Mailpit (local mail: password resets, verification): http://localhost:8025

4. (Optional) seed real documented vessels as textbook cases (fill in current
   MMSIs in `backend/app/seed.py`, look them up by IMO on vesselfinder.com):

   ```sh
   docker compose exec backend python -m app.seed
   ```

Risk lists import automatically on first boot and daily at 04:00 UTC; no manual
step is needed.

## Without Docker

Run Postgres 16 and Mailpit, then:

```sh
cd backend && pip install -r requirements.txt && uvicorn app.main:app --reload
cd frontend && npm install && npm run dev
```

## Behaviour engine

The engine scans the live feed every 10 minutes. Each rule emits a scored
`RiskEvent`; scores are summed over a 30-day window.

| Rule | Signal | Score |
|---|---|---|
| `risklist_ofac` | IMO on the OFAC sanctions list | 100 |
| `risklist_*` (sanctions) | IMO on UK / EU / GUR / Canada / Australia / Switzerland / UN 1718 / UANI / KSE lists | 80 |
| `risklist_iuu`, `risklist_eu_iuu` | IMO on an RFMO / EU illegal-fishing blacklist | 80 |
| `risklist_parismou` | refused EU port access (Paris MoU banning list) | 70 |
| `risklist_tokyomou` / `blackseamou` / `abujamou` | port-control detention (corroboration only, kept below the add threshold) | 25 |
| `identity_change` | transmitted a different name/IMO/callsign than before | 40 |
| `mmsi_collision` | one identity broadcasting two coexisting tracks (cloned identity) | 45 |
| `draught_change` | draught changed at sea near an STS region (possible cargo transfer) | 45 |
| `oil_slick` | SAR oil slick at a transfer point | 45 |
| `circle_spoofing` | GNSS circle-spoofing (fake geometric track while working elsewhere) | 40 |
| `impossible_jump` | consecutive positions implying > 40 kn (spoofing) | 35 |
| `flag_hop` | one IMO reflagged across many MMSIs | 35 |
| `regional_gap` | went silent inside covered water while underway, away from box edges | 30 |
| `drift_mismatch` | reappeared far from the predicted drift position after a gap | 30 |
| `loitering` | sustained near-stationary dwell well offshore in a trafficking corridor | 30 |
| `identity_integrity` | fabricated identity (malformed/factory-default MMSI, bad country prefix) | 30 |
| `nav_status_lie` | broadcasts "anchored/moored" while clearly moving | 30 |
| `gfw_encounter` | Global Fishing Watch: met another vessel at sea (transshipment) | 30 |
| `gfw_ais_gap` | Global Fishing Watch: disabled AIS | 30 |
| `gfw_loitering` | Global Fishing Watch: loitered offshore | 25 |
| `rendezvous` | two cargo/tanker ships < 500 m apart, near-stationary, offshore, both recently underway | 25 |
| `midsea_appearance` | brand-new MMSI first seen in open water (corroboration only) | 15 |

The MMSI-to-IMO bridge comes from AIS `ShipStaticData` messages collected into
our own `vessel_registry`.

### Watchlist qualification (trustworthiness)

A vessel is added to the shared Suggestions/`vessels` feed only when it has a
**hard anchor**, not just accumulated soft signals:

- a **government sanction / fishing designation** (any `risklist_` except the
  detention MoUs), **or**
- a **hard behavioural signal**: `identity_change`, `mmsi_collision`,
  `circle_spoofing`, `impossible_jump`, `identity_integrity`, `draught_change`.

Soft behaviour (loitering, midsea appearance, GFW events) and PSC detentions
are **corroboration only**: they raise the score of an already-anchored ship
but never put a vessel on the list by themselves. This keeps the feed
fact-based rather than flooded with substandard-but-legal ships. Records are
written in plain language (for example "detained by port control (Tokyo MoU)",
not `risklist_tokyomou`).

## How it works

| Component | File | Behaviour |
|---|---|---|
| AIS ingestor | `backend/app/ingest/aisstream.py` | one WebSocket, global bbox, reconnect with backoff; every ship stored 1/min (in-region positions tagged `region`, the rest `world`); Null Island (0,0) and out-of-range coordinates dropped; batched inserts every 2s |
| Behaviour engine | `backend/app/jobs/behavior.py` | every 10 min; runs all rules, scores vessels, applies the qualification logic above |
| Gap detector | `backend/app/jobs/gap_detector.py` | every 5 min; opens a gap when the last position is older than the threshold and the ship was underway; closes on reappearance and computes displacement |
| Overpass matcher | `backend/app/services/copernicus.py` | searches Sentinel-1 GRD + Sentinel-2 L1C in the drift area within the gap window; re-runs every 6 hours for open gaps |
| Oil-slick sync | `backend/app/services/*` | daily; attaches SAR oil-slick detections near transfer points |
| GFW sync | `backend/app/services/gfw.py` | every 6 hours; imports encounter / AIS-gap / loitering events, linked by MMSI |
| Risk-list import | `backend/app/services/risklists.py` | daily at 04:00 UTC; refreshes every sanction/detention/fishing list |
| Retention | `backend/app/jobs/retention.py` | daily at 03:30 UTC; prunes old ambient history |
| Per-user watchlist | `backend/app/api/user_watchlist.py` | `/api/me/watchlist` served from our own DB |
| Per-user events | `backend/app/api/user_events.py` | `/api/me/events` risk-event feed for the user's followed ships |
| Accounts | `backend/app/auth.py`, `backend/app/main.py` | FastAPI-Users: register / login (JWT) / reset password / Google OAuth; mail via Mailpit |

## What we store and database growth

We persist history for **every** ship the terrestrial feed sees, so any vessel
can be located and its track reconstructed after the fact. Storage is throttled
so disk stays bounded:

| Feed | Source tag | Throttle | Purpose |
|---|---|---|---|
| In-region traffic (configured regions) | `region` | 1 / min | what the behaviour detectors run on |
| Everything else (global coastal) | `world` | 1 / min | locate/track any ship, forensic replay |

Coverage is terrestrial AIS (~200 km offshore); open ocean needs satellite AIS
(phase 2). The `positions` table dominates storage: at a 1-min throttle the
feed lands ~5-7M rows/day, settling around **~80-100 GB / ~500M rows** at 90-day
retention. Point queries (a ship's track by MMSI+time) stay fast via the
`(mmsi, ts)` index regardless.

Retention caps this (detectors only look back <=36 days, so 90 days is safe):
- ambient (`region` + `world`) pruned past `POSITIONS_RETENTION_DAYS` (90)
- watchlist positions kept `WATCHLIST_RETENTION_DAYS` (400) as the evidence trail
- oil slicks older than 180 days dropped

If retention is extended or the feed widened, **monthly RANGE partitioning** of
`positions` (drop old partitions instantly, no DELETE churn) is the next scale
step: documented but not yet implemented, since DELETE-based retention handles
current volume.

## Serving under load

The map endpoints return one global snapshot identical for every visitor, so
they are cached rather than recomputed per request:

- `GET /positions/latest` is a ~1 s DISTINCT-ON query building a ~4 MB payload,
  cached for 15 s as **pre-gzipped bytes** behind a lock so a cache miss
  triggers exactly one rebuild. Responses carry `Cache-Control: public,
  max-age=15`.
- `GET /positions/world` is the in-memory AIS snapshot with `max-age=30`.

Measured locally (single dev worker, `--reload`): a 500-request concurrent
burst went from ~7 req/s / 6.4 s median latency (uncached) to **~870 req/s /
46 ms median**. 10k concurrent viewers polling `/positions/latest` every 30 s is
~330 req/s, comfortably within that, and the DB does ~1 query per 15 s
regardless of viewer count. For real 10k-scale, put a **CDN or nginx** in front:
the `Cache-Control` headers let it serve the shared snapshot to everyone.

**Scaling caveat:** the AIS ingester and the APScheduler jobs run in-process via
the app lifespan, so the API must run as a **single process** (adding
`uvicorn --workers N` would start N ingest connections and N schedulers). One
worker already exceeds the required throughput, so front it with a proxy/CDN;
horizontal API scaling would first require splitting the ingester/scheduler into
their own service.

## Licensing

Product is intended to go commercial, but several sources are non-commercial
(CC BY-NC): OpenSanctions-derived lists (GUR, Canada, Australia, Switzerland,
UANI), the RFMO IUU list (iuu-vessels.org), and Global Fishing Watch. The EU
IUU list is official EU law and commercial-safe. Before commercial launch these
need government-direct swaps. See `LICENSING.md` for the per-source breakdown.

## Known limitations (deliberate, phase 1)

- Terrestrial AIS reaches ~200 km offshore; beyond that a gap cannot be
  distinguished from "out of range". Satellite overpasses are a sample (every
  few days), not continuous coverage; that is what satellite AIS (phase 2) is
  for.
- Automated SAR ship detection runs on every stored Sentinel-1 position check
  when `CDSE_SH_CLIENT_ID`/`CDSE_SH_CLIENT_SECRET` are set (free Copernicus
  Data Space OAuth client): a 3x3 km sigma0 chip around the claimed position
  is fetched via the Sentinel Hub Process API, a CFAR-style detector marks
  bright radar targets, and the verdict ("radar target at claimed spot" /
  "no target within 500 m") plus the chip PNG (stored on MinIO/R2) appear
  under the satellite cross-checks. It detects *bright targets*, not
  identified hulls - breakwaters and islets reflect too - so the Copernicus
  Browser link stays next to every verdict for human confirmation. Without
  credentials the check remains human-only via that link.
- Copernicus quicklooks may require a login; the frontend then falls back to
  the browser link only.
- GFW SAR detection points are not available on the free API tier.
