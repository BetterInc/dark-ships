import asyncio
import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from sqlalchemy import text

from .api import admin, gaps, imagery, positions, suggestions, user_events, user_watchlist, vessels
from .auth import (
    UserCreate,
    UserRead,
    UserUpdate,
    auth_backend,
    fastapi_users,
    google_oauth_client,
)
from .config import get_settings
from .db import engine
from .ingest.aisstream import start_ingest
from .jobs.behavior import run_behavior_scan
from .jobs.gap_detector import run_gap_detection
from .jobs.partitions import ensure_partitions
from .jobs.position_checker import run_position_checks
from .jobs.retention import prune_positions
from .models import Base
from .services.cerulean import sync_slicks
from .services.gfw import sync_gfw_events
from .services.risklists import import_all

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # BRIN on ts: cheap index for the append-only position stream
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_positions_ts_brin ON positions USING brin (ts)"
        ))
        # lightweight migrations for columns added after the first release
        for ddl in (
            "ALTER TABLE vessels ADD COLUMN IF NOT EXISTS pinned BOOLEAN NOT NULL DEFAULT TRUE",
            "ALTER TABLE vessels ADD COLUMN IF NOT EXISTS auto_added BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE vessels ADD COLUMN IF NOT EXISTS risk_score DOUBLE PRECISION NOT NULL DEFAULT 0",
            "ALTER TABLE vessels ADD COLUMN IF NOT EXISTS followed BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE vessel_registry ADD COLUMN IF NOT EXISTS draught DOUBLE PRECISION",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(16) NOT NULL DEFAULT 'user'",
            # backfill accounts promoted before the role column existed
            "UPDATE users SET role = 'admin' WHERE is_superuser AND role = 'user'",
        ):
            await conn.execute(text(ddl))
        # promote already-registered admin accounts (idempotent; new signups
        # are promoted in UserManager.on_after_register instead)
        admin_emails = sorted(get_settings().admin_email_set)
        if admin_emails:
            await conn.execute(
                # also verified: login requires it, and an admin locked out of
                # their own instance helps nobody
                text("UPDATE users SET is_superuser = TRUE, role = 'admin', "
                     "is_verified = TRUE WHERE lower(email) = ANY(:emails)"),
                {"emails": admin_emails},
            )
    # positions is monthly-partitioned: make sure the current + upcoming month
    # partitions exist before ingest starts inserting.
    await ensure_partitions()
    # one-time seed of the per-ship current-picture table (kept fresh by the
    # ingester's flush upserts thereafter): pay the history scan ONCE here,
    # never again on the request path
    async with engine.begin() as conn:
        seeded = await conn.scalar(text("SELECT EXISTS (SELECT 1 FROM latest_positions)"))
        if not seeded:
            await conn.execute(text(
                "INSERT INTO latest_positions "
                "SELECT DISTINCT ON (mmsi) mmsi, ts, lat, lon, sog, cog, heading, "
                "       nav_status, ship_name, source "
                "FROM positions WHERE ts >= now() - interval '7 days' "
                "ORDER BY mmsi, ts DESC "
                "ON CONFLICT (mmsi) DO NOTHING"
            ))
            logger.info("Seeded latest_positions from position history")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    tasks: list[asyncio.Task] = []
    scheduler: AsyncIOScheduler | None = None

    if not settings.runs_worker:
        # API-only instance: serve HTTP, read the DB/cache. The singleton
        # ingester + scheduler live in the separate worker, so we start neither
        # here - this is what lets the API scale to N replicas safely.
        logger.info("Starting in WEB mode (run_mode=%s): API only, no ingest/scheduler",
                    settings.run_mode)
        yield
        return

    # worker / all: this instance owns schema + ingest + all scheduled jobs.
    # It must run as a SINGLE replica (two would double every ingest + job).
    logger.info("Starting in %s mode: ingester + scheduler active",
                settings.run_mode.upper())
    await init_db()
    start_ingest(tasks)

    scheduler = AsyncIOScheduler(timezone="UTC")
    # NOTE: no next_run_time=None here - in APScheduler that adds the job
    # PAUSED (it never fires), not "first run after one interval". The
    # _initial_* tasks below cover the immediate run; the interval default
    # (first fire one interval after start) avoids doubling up.
    scheduler.add_job(run_gap_detection, "interval", minutes=5)
    scheduler.add_job(run_behavior_scan, "interval", minutes=10)
    scheduler.add_job(run_position_checks, "interval", hours=6)
    scheduler.add_job(import_all, "cron", hour=4, minute=0)
    scheduler.add_job(sync_slicks, "cron", hour=5, minute=0)
    scheduler.add_job(sync_gfw_events, "interval", hours=6)
    scheduler.add_job(prune_positions, "cron", hour=3, minute=30)
    # pre-create next month's partition a few days early
    scheduler.add_job(ensure_partitions, "cron", day=25, hour=2, minute=0)
    scheduler.start()
    # First runs shortly after startup, so you don't have to wait
    tasks.append(asyncio.create_task(_initial_gap_run()))
    tasks.append(asyncio.create_task(_initial_behavior_run()))

    yield

    scheduler.shutdown(wait=False)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _initial_gap_run() -> None:
    await asyncio.sleep(15)
    try:
        await run_gap_detection()
    except Exception:
        logger.exception("Initial gap detection run failed")


async def _initial_behavior_run() -> None:
    await asyncio.sleep(30)
    try:
        await import_all()  # no-op if lists are < 20h old
    except Exception:
        logger.exception("Initial risk-list import failed")
    # scan BEFORE the external syncs: a slow/hung Copernicus or GFW call must
    # not starve the sanctions matcher (it once sat behind a hung GFW sync
    # for the pod's whole life)
    try:
        await run_behavior_scan()
    except Exception:
        logger.exception("Initial behaviour scan failed")
    try:
        await sync_slicks()  # 90-day backfill on first run, 7-day refresh after
    except Exception:
        logger.exception("Initial oil-slick sync failed")
    try:
        await sync_gfw_events()  # GFW behavioural events into the engine
    except Exception:
        logger.exception("Initial GFW event sync failed")
    try:
        await run_position_checks()
    except Exception:
        logger.exception("Initial position-check run failed")


app = FastAPI(title="Dark Ships - phase 1 MVP", lifespan=lifespan)

# the position payloads are large (~4 MB) but highly compressible JSON; gzip
# cuts them ~10x on the wire, which matters a lot under many concurrent viewers
app.add_middleware(GZipMiddleware, minimum_size=1024)

# Allowed browser origins: localhost (dev) + the configured frontend + any
# extra CORS_ORIGINS. Cloudflare Pages preview deployments (*.pages.dev) are
# matched by regex so previews work without listing each one.
_cors = get_settings()
_origins = {"http://localhost:5173", "http://127.0.0.1:5173", _cors.frontend_base_url}
_origins |= {o.strip() for o in _cors.cors_origins.split(",") if o.strip()}
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(_origins),
    allow_origin_regex=r"https://[a-z0-9-]+\.dark-ships\.pages\.dev",
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(vessels.router)
app.include_router(positions.router)
app.include_router(gaps.router)
app.include_router(suggestions.router)
app.include_router(imagery.router)
app.include_router(user_watchlist.router)
app.include_router(user_events.router)
app.include_router(admin.router)

# ---- auth / accounts (FastAPI-Users), all under /api --------------------
app.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
    prefix="/api/auth",
    tags=["auth"],
)
app.include_router(
    # unverified accounts cannot log in (LOGIN_USER_NOT_VERIFIED) - the frontend
    # offers a resend, and /api/auth/verify flips the flag
    fastapi_users.get_auth_router(auth_backend, requires_verification=True),
    prefix="/api/auth/jwt",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_reset_password_router(),
    prefix="/api/auth",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_verify_router(UserRead),
    prefix="/api/auth",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_users_router(UserRead, UserUpdate),
    prefix="/api/users",
    tags=["users"],
)

# Google login is only mounted when both creds are configured.
if google_oauth_client is not None:
    app.include_router(
        fastapi_users.get_oauth_router(
            google_oauth_client,
            auth_backend,
            get_settings().auth_secret,
            associate_by_email=True,
            is_verified_by_default=True,
        ),
        prefix="/api/auth/google",
        tags=["auth"],
    )


@app.get("/api/health")
async def health():
    # version is the deployed image SHA (set via APP_VERSION build arg); the CI
    # smoke test polls this until it reports the just-rolled tag.
    import os
    return {"status": "ok", "version": os.getenv("APP_VERSION", "dev"),
            "role": get_settings().run_mode}
