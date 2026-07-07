"""Monthly RANGE partitioning for the `positions` table.

The parent table is created by SQLAlchemy (Position has
`postgresql_partition_by='RANGE (ts)'`). Postgres does NOT auto-create the
month child partitions, so we manage them here:

- `ensure_partitions()` pre-creates the current month plus a few ahead (and a
  DEFAULT partition as a safety net) so inserts always land somewhere. Run at
  startup and monthly.
- `list_month_partitions()` / `drop_partition()` support retention: a whole
  month is dropped in O(1) once it is older than the hot window (and has been
  exported to the cold tier first - see services/cold.py).
"""

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text

from ..db import engine

logger = logging.getLogger(__name__)

PARENT = "positions"


def _month_floor(d: date) -> date:
    return d.replace(day=1)


def _next_month(d: date) -> date:
    return date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)


def _partition_name(d: date) -> str:
    return f"{PARENT}_{d.year:04d}_{d.month:02d}"


async def _positions_is_partitioned(conn) -> bool:
    return bool(await conn.scalar(text(
        "SELECT EXISTS (SELECT 1 FROM pg_partitioned_table pt "
        "JOIN pg_class c ON c.oid = pt.partrelid WHERE c.relname = :n)"), {"n": PARENT}))


async def ensure_partitions(months_ahead: int = 3) -> None:
    """Create the DEFAULT partition and month partitions for [last month .. now
    + months_ahead], if they don't already exist. Idempotent."""
    async with engine.begin() as conn:
        if not await _positions_is_partitioned(conn):
            logger.warning(
                "Table '%s' is not partitioned - cold-tier partitioning is "
                "disabled. Wipe the DB (docker compose down -v) so it is "
                "recreated as a partitioned table.", PARENT)
            return

        # safety net so an out-of-range ts never fails an insert
        await conn.execute(text(
            f"CREATE TABLE IF NOT EXISTS {PARENT}_default PARTITION OF {PARENT} DEFAULT"))

        start = _month_floor((datetime.now(timezone.utc) - timedelta(days=31)).date())
        d = start
        created = []
        for _ in range(months_ahead + 2):
            name = _partition_name(d)
            nxt = _next_month(d)
            await conn.execute(text(
                f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF {PARENT} "
                f"FOR VALUES FROM ('{d.isoformat()}') TO ('{nxt.isoformat()}')"))
            created.append(name)
            d = nxt
    logger.info("Ensured position partitions: %s (+ default)", ", ".join(created))


async def list_month_partitions() -> list[tuple[str, date, date]]:
    """All month partitions as (name, range_start, range_end), oldest first.
    Excludes the DEFAULT partition."""
    async with engine.connect() as conn:
        rows = (await conn.execute(text(
            """
            SELECT c.relname
            FROM pg_inherits i
            JOIN pg_class c   ON c.oid = i.inhrelid
            JOIN pg_class p   ON p.oid = i.inhparent
            WHERE p.relname = :parent
              AND c.relname ~ '^positions_[0-9]{4}_[0-9]{2}$'
            ORDER BY c.relname
            """), {"parent": PARENT})).all()
    out = []
    for (name,) in rows:
        _, y, m = name.rsplit("_", 2)
        start = date(int(y), int(m), 1)
        out.append((name, start, _next_month(start)))
    return out


async def drop_partition(name: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {name}"))
    logger.info("Dropped position partition %s", name)
