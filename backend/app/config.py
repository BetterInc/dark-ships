import json
from functools import lru_cache

from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Region(BaseModel):
    name: str
    # [[latMin, lonMin], [latMax, lonMax]]
    bbox: list[list[float]]
    # "sts" = anchoring is normal here (EOPL, Hormuz) - loitering rule skips it;
    # "transit" = a ship dwelling offshore here is suspicious (narco corridors)
    kind: str = "transit"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://darkships:darkships@localhost:5432/darkships"

    aisstream_api_key_watchlist: str = ""
    aisstream_api_key_regions: str = ""
    ais_regions: list[Region] = []
    # Subscribe globally on the regions connection. We now persist history for
    # EVERY ship so any vessel can be located/tracked after the fact. The curated
    # region feed is kept at fine resolution (region_throttle_seconds); the wider
    # world feed is stored more coarsely (world_throttle_seconds) to cap disk.
    ais_world_feed: bool = True

    gap_threshold_hours: float = 6.0
    gap_min_sog_knots: float = 1.0
    region_throttle_seconds: int = 60    # in-region ambient (22 regions): 1 position/min
    world_throttle_seconds: int = 60     # out-of-region world: 1 position/min
    drift_max_nm: float = 100.0

    # Retention: prune ambient position history past this age to cap disk use
    # (~215 GB/yr unmanaged). Detectors look back <=36 days, so 90 is safe.
    # Watchlist ships' history is kept longer.
    positions_retention_days: int = 90
    watchlist_retention_days: int = 400

    # Behaviour engine / public Suggestions. The engine populates the shared
    # `vessels` table (auto_added) that powers the public Suggestions feed.
    auto_watchlist: bool = True
    # Only VERIFIED facts auto-promote: a ship on an official sanctions/ban
    # list is watchlisted automatically (and colored by category on the map);
    # behaviour-only flags (loitering, gaps, identity changes) stay in
    # Suggestions until a human reviews them. Same default everywhere -
    # local dev and prod behave identically.
    auto_watchlist_sanctions_only: bool = True
    # Legacy cap for the old global-watchlist "followed" flag on /api/vessels.
    # There is no dedicated provider connection anymore (one ingestion path),
    # so this only bounds that out-of-scope CRUD endpoint.
    follow_max: int = 50

    # Per-user private watchlist: free tier ship cap.
    free_follow_limit: int = 10
    suggestion_min_score: float = 50.0
    regional_gap_hours: float = 3.0    # silence threshold inside a covered region
    regional_gap_min_sog: float = 3.0  # ship must have been underway
    edge_margin_km: float = 15.0       # "interior" distance from the bbox edge
    rendezvous_margin_km: float = 30.0
    impossible_speed_knots: float = 40.0

    gfw_api_token: str = ""

    # Process role. The AIS ingester and the APScheduler jobs are singletons and
    # MUST run in exactly one instance, so in production the API scales freely
    # (RUN_MODE=web) while a single-replica worker (RUN_MODE=worker) owns ingest
    # + scheduling + DB migrations. Locally, one combined process (RUN_MODE=all).
    run_mode: str = "all"  # all | web | worker

    @property
    def runs_worker(self) -> bool:
        return self.run_mode in ("worker", "all")

    @property
    def serves_api(self) -> bool:
        return self.run_mode in ("web", "all")

    # Cold tier: position partitions older than positions_retention_days are
    # exported to Parquet on S3-compatible storage (MinIO locally, Cloudflare R2
    # in prod) and then dropped from Postgres. Same code for both - only the
    # endpoint/keys change. Leave blank to disable (partitions are then kept,
    # never dropped, so no data is lost silently).
    s3_endpoint: str = ""            # http://minio:9000  |  https://<acct>.r2.cloudflarestorage.com
    s3_bucket: str = ""              # e.g. darkships-cold
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_region: str = "auto"          # "auto" for R2; any value (e.g. us-east-1) for MinIO

    @property
    def cold_storage_enabled(self) -> bool:
        return bool(self.s3_endpoint and self.s3_bucket
                    and self.s3_access_key_id and self.s3_secret_access_key)

    # Auth / accounts (FastAPI-Users). auth_secret signs the JWTs and the
    # password-reset / verification tokens - override via AUTH_SECRET in prod.
    auth_secret: str = "dev-insecure-change-me"
    frontend_base_url: str = "http://localhost:5173"
    # extra allowed CORS origins (comma-separated), on top of localhost +
    # frontend_base_url. Cloudflare Pages preview URLs (*.pages.dev) are matched
    # by regex in main.py so they don't need listing here.
    cors_origins: str = ""

    # Local mail (Mailpit in dev): the backend reaches it by service name.
    mail_host: str = "localhost"
    mail_port: int = 1025
    mail_from: str = "noreply@darkships.local"
    # Prod: HTTPS relay next to Mailu (Scaleway blocks outbound SMTP from the
    # cluster). When set, mail.py POSTs here instead of speaking SMTP.
    mail_relay_url: str = ""
    mail_relay_token: str = ""

    # Google login stays disabled until both creds are filled in.
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""

    @field_validator("ais_regions", mode="before")
    @classmethod
    def parse_regions(cls, v):
        if isinstance(v, str):
            return json.loads(v) if v.strip() else []
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
