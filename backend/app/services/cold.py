"""Cold tier: export old position partitions to Parquet on S3-compatible
storage (MinIO locally, Cloudflare R2 in prod) and query them back.

DuckDB does the heavy lifting: it reads a Postgres partition via the `postgres`
extension and writes compressed Parquet straight to S3 via `httpfs`. The same
engine reads that Parquet back and can UNION it with the live (hot) Postgres
table, so a single query spans hot + cold. R2 and MinIO are both S3-compatible,
so only the endpoint and keys differ.

DuckDB calls are blocking, so the async helpers hop to a worker thread.
"""

import asyncio
import logging
from datetime import datetime
from urllib.parse import urlparse

from ..config import get_settings

logger = logging.getLogger(__name__)

# columns we keep in the cold archive / return from a track query
_COLS = "mmsi, ts, lat, lon, sog, cog, heading, nav_status, ship_name, source"


def _pg_dsn() -> str:
    """libpq DSN for DuckDB's postgres extension, derived from database_url."""
    u = urlparse(get_settings().database_url.replace("+asyncpg", ""))
    return (f"host={u.hostname} port={u.port or 5432} dbname={u.path.lstrip('/')} "
            f"user={u.username} password={u.password}")


def _s3_host_ssl() -> tuple[str, bool]:
    """DuckDB wants the endpoint as host[:port] without a scheme, plus a
    separate SSL flag. http://minio:9000 -> ('minio:9000', False)."""
    ep = get_settings().s3_endpoint
    if "://" in ep:
        p = urlparse(ep)
        return p.netloc, p.scheme == "https"
    return ep, True


def _connect(attach_pg: bool = False):
    import duckdb

    s = get_settings()
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    host, ssl = _s3_host_ssl()
    con.execute("SET s3_url_style='path'")
    con.execute(f"SET s3_endpoint='{host}'")
    con.execute(f"SET s3_use_ssl={'true' if ssl else 'false'}")
    con.execute(f"SET s3_region='{s.s3_region}'")
    con.execute(f"SET s3_access_key_id='{s.s3_access_key_id}'")
    con.execute(f"SET s3_secret_access_key='{s.s3_secret_access_key}'")
    if attach_pg:
        con.execute("INSTALL postgres; LOAD postgres;")
        con.execute(f"ATTACH '{_pg_dsn()}' AS pg (TYPE postgres, READ_ONLY)")
    return con


def _glob() -> str:
    return f"s3://{get_settings().s3_bucket}/positions/*/*/data.parquet"


def _month_key(year: int, month: int) -> str:
    b = get_settings().s3_bucket
    return f"s3://{b}/positions/year={year:04d}/month={month:02d}/data.parquet"


# ---- export (blocking) ---------------------------------------------------

def _export_partition(name: str) -> int:
    """Copy one month partition (e.g. positions_2026_05) to Parquet on S3.
    Returns the row count written. Overwrites any existing file for the month."""
    _, y, m = name.rsplit("_", 2)
    key = _month_key(int(y), int(m))
    con = _connect(attach_pg=True)
    try:
        con.execute(
            f"COPY (SELECT {_COLS} FROM pg.public.{name}) TO '{key}' "
            "(FORMAT parquet, COMPRESSION zstd, OVERWRITE_OR_IGNORE true)")
        return con.execute(f"SELECT count(*) FROM read_parquet('{key}')").fetchone()[0]
    finally:
        con.close()


# ---- query (blocking) ----------------------------------------------------

def _has_cold(con) -> bool:
    try:
        return con.execute(f"SELECT count(*) FROM glob('{_glob()}')").fetchone()[0] > 0
    except Exception:
        return False


def _query_track(mmsi: int, start: datetime, end: datetime) -> list[dict]:
    """Positions for one vessel between start and end, spanning hot (Postgres)
    and cold (Parquet on S3) transparently, oldest first."""
    con = _connect(attach_pg=True)
    try:
        hot = ("SELECT mmsi, ts, lat, lon, sog, cog FROM pg.public.positions "
               "WHERE mmsi = ? AND ts >= ? AND ts < ?")
        params = [mmsi, start, end]
        if _has_cold(con):
            cold = (f"SELECT mmsi, ts, lat, lon, sog, cog FROM read_parquet('{_glob()}', "
                    "hive_partitioning=true, union_by_name=true) "
                    "WHERE mmsi = ? AND ts >= ? AND ts < ?")
            sql = f"{hot} UNION ALL {cold} ORDER BY ts"
            params = params + [mmsi, start, end]
        else:
            sql = f"{hot} ORDER BY ts"
        rows = con.execute(sql, params).fetchall()
        return [{"mmsi": r[0], "ts": r[1].isoformat(), "lat": r[2],
                 "lon": r[3], "sog": r[4], "cog": r[5]} for r in rows]
    finally:
        con.close()


def _archived_months() -> list[dict]:
    con = _connect()
    try:
        if not _has_cold(con):
            return []
        rows = con.execute(
            f"SELECT year, month, count(*) AS rows FROM read_parquet('{_glob()}', "
            "hive_partitioning=true, union_by_name=true) GROUP BY year, month ORDER BY year, month"
        ).fetchall()
        return [{"year": int(r[0]), "month": int(r[1]), "rows": int(r[2])} for r in rows]
    finally:
        con.close()


# ---- async wrappers ------------------------------------------------------

async def export_partition(name: str) -> int:
    return await asyncio.to_thread(_export_partition, name)


async def query_track(mmsi: int, start: datetime, end: datetime) -> list[dict]:
    return await asyncio.to_thread(_query_track, mmsi, start, end)


async def archived_months() -> list[dict]:
    return await asyncio.to_thread(_archived_months)
