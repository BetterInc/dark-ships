import asyncio
import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .api import admin, imagery, positions, suggestions, user_events, user_watchlist, vessels
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
from .jobs.sar_detector import run_sar_detection
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
            # automated SAR ship detection on stored position checks
            "ALTER TABLE position_checks ADD COLUMN IF NOT EXISTS analyzed_at TIMESTAMPTZ",
            "ALTER TABLE position_checks ADD COLUMN IF NOT EXISTS hull_detected BOOLEAN",
            "ALTER TABLE position_checks ADD COLUMN IF NOT EXISTS target_count INTEGER",
            "ALTER TABLE position_checks ADD COLUMN IF NOT EXISTS nearest_offset_m DOUBLE PRECISION",
            "ALTER TABLE position_checks ADD COLUMN IF NOT EXISTS persistent_target BOOLEAN",
            "ALTER TABLE position_checks ADD COLUMN IF NOT EXISTS chip_key VARCHAR(256)",
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
    # Seatbelt: never run on the insecure default JWT secret outside an
    # explicit dev/test environment - it would let anyone forge a superuser
    # token or a password-reset token. Fails closed: `environment` defaults to
    # "production", so forgetting to set ENVIRONMENT does NOT bypass this.
    if settings.auth_secret == "dev-insecure-change-me" and \
            settings.environment not in ("dev", "test", "local"):
        raise RuntimeError(
            f"AUTH_SECRET is unset (still the insecure default) with "
            f"ENVIRONMENT={settings.environment!r}. Set a strong AUTH_SECRET, "
            f"or set ENVIRONMENT=dev for local development."
        )
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
    # offset from the checker so fresh checks from a run get analyzed ~1h later
    scheduler.add_job(run_sar_detection, "interval", hours=6, minutes=60)
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
    try:
        await run_sar_detection()
    except Exception:
        logger.exception("Initial SAR detection run failed")


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


# ---- rate limiting (built on the `limits` library) --------------------------
# Two concerns, one middleware:
#   1. Brute-force / abuse on the auth-write endpoints.
#   2. Guests hammering the public map reads (bandwidth / scraping).
#
# Design choices that matter:
#   * Login is keyed PER ACCOUNT (email), not per IP: whole mobile networks and
#     offices share one IP (CGNAT), so an IP cap would punish unrelated users.
#     A brute-force targets one account, so a per-email budget is both the right
#     defence AND shared-IP-safe. A loose per-IP guard still stops a firehose.
#   * Public GET reads are TIERED: authenticated requests are keyed by the
#     verified user id (signature+exp checked in-process, no DB hit, so the tier
#     can't be spoofed by sending any bearer) and get a much higher ceiling;
#     anonymous traffic shares a lower per-IP guest pool. Registered users thus
#     get a real advantage and are unaffected by noisy neighbours on their IP.
#
# Storage is the `limits` async backend from RATELIMIT_STORAGE_URI: per-pod
# memory by default, or a shared Redis/Valkey to make the caps global across
# replicas. Volumetric DDoS from many IPs still belongs at the edge/CDN.
from fastapi_users.jwt import decode_jwt
from limits import parse as _rl_parse
from limits.aio.strategies import MovingWindowRateLimiter
from limits.storage import storage_from_string as _rl_storage_from_string  # dispatches async+ URIs

_rl_limiter = MovingWindowRateLimiter(
    _rl_storage_from_string(get_settings().ratelimit_storage_uri))

_LOGIN_PATH = "/api/auth/jwt/login"
_RL_LOGIN_ACCT = _rl_parse("10/5minutes")   # per email - real brute-force cap
_RL_LOGIN_IP = _rl_parse("100/minute")      # per IP - coarse firehose guard
_RL_WRITE = {                               # per IP, email-sending / write paths
    "/api/auth/register": _rl_parse("20/minute"),
    "/api/auth/forgot-password": _rl_parse("10/minute"),
    "/api/auth/reset-password": _rl_parse("20/minute"),
    "/api/auth/request-verify-token": _rl_parse("10/minute"),
}
_RL_READ_GUEST = _rl_parse("120/minute")    # anonymous map reads, per IP
_RL_READ_USER = _rl_parse("600/minute")     # signed-in reads, per user id
_RL_MESSAGE = {"detail": "Too many requests. Please wait a moment and try again."}


def _client_ip(request: Request) -> str:
    # Rate-limit key: use the RIGHTMOST X-Forwarded-For hop, i.e. the address
    # our own ingress appended = the peer that actually connected to it. The
    # LEFTMOST entry is fully client-controlled, so keying off it lets an
    # attacker rotate a fake IP per request and defeat every per-IP limit
    # (login/register/forgot-password throttles). A client can prepend fake
    # hops but cannot forge the one infrastructure adds last.
    # NOTE: if a trusted CDN (e.g. Cloudflare) is ever put in front of the API,
    # switch to its verified client-IP header instead of XFF.
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        hops = [h.strip() for h in fwd.split(",") if h.strip()]
        if hops:
            return hops[-1]
    return request.client.host if request.client else "unknown"


def _verified_user_id(request: Request) -> str | None:
    """User id from a valid Bearer token (signature + expiry + audience checked,
    no DB lookup). None for anonymous or invalid tokens."""
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    try:
        data = decode_jwt(auth[7:].strip(), get_settings().auth_secret,
                          ["fastapi-users:auth"], algorithms=["HS256"])
        return str(data.get("sub")) if data.get("sub") is not None else None
    except Exception:
        return None


async def _login_email(request: Request) -> str | None:
    # form-urlencoded body; reading it here is safe - Starlette caches the body
    # so the downstream login handler still parses the same form.
    try:
        from urllib.parse import parse_qs
        body = (await request.body()).decode("utf-8", "ignore")
        vals = parse_qs(body).get("username")
        return vals[0].strip().lower() if vals else None
    except Exception:
        return None


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    path = request.url.path
    if request.method == "POST":
        if path == _LOGIN_PATH:
            if not await _rl_limiter.hit(_RL_LOGIN_IP, "login-ip", _client_ip(request)):
                return JSONResponse(_RL_MESSAGE, status_code=429)
            email = await _login_email(request)
            if email and not await _rl_limiter.hit(_RL_LOGIN_ACCT, "login-acct", email):
                return JSONResponse(_RL_MESSAGE, status_code=429)
        elif path in _RL_WRITE:
            if not await _rl_limiter.hit(_RL_WRITE[path], path, _client_ip(request)):
                return JSONResponse(_RL_MESSAGE, status_code=429)
    elif request.method == "GET" and path.startswith("/api/") and path != "/api/health":
        uid = _verified_user_id(request)
        if uid is not None:
            allowed = await _rl_limiter.hit(_RL_READ_USER, "read-user", uid)
        else:
            allowed = await _rl_limiter.hit(_RL_READ_GUEST, "read-guest", _client_ip(request))
        if not allowed:
            return JSONResponse(_RL_MESSAGE, status_code=429)
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return resp

app.include_router(vessels.router)
app.include_router(positions.router)
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
