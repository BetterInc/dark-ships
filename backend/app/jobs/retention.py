"""Data retention for the append-only `positions` stream.

`positions` is monthly RANGE-partitioned. Retention is now a DROP of whole
month-partitions (instant, no DELETE churn or vacuum). Before a month is
dropped it is exported to Parquet on the cold tier (services/cold.py), so long
history stays cheap and queryable rather than being lost. If the cold tier is
not configured, partitions are KEPT (never silently deleted).

The small non-partitioned side tables (oil slicks, draught changes) are still
pruned with plain DELETE - they are tiny and short-lived. Runs daily.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from ..config import get_settings
from ..db import SessionLocal
from ..models import DraughtChange, OilSlick
from ..services import cold
from .partitions import drop_partition, ensure_partitions, list_month_partitions

logger = logging.getLogger(__name__)


async def prune_positions() -> None:
    s = get_settings()
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=s.positions_retention_days)).date()

    # keep the next few months of partitions ready before we prune old ones
    await ensure_partitions()

    # archive + drop every month-partition entirely older than the hot window
    archived = dropped = kept = 0
    for name, _start, end in await list_month_partitions():
        if end > cutoff:
            continue  # still (partly) inside the hot window
        if not s.cold_storage_enabled:
            kept += 1
            logger.warning("Cold storage not configured - keeping old partition %s "
                           "(set S3_* to archive + drop it)", name)
            continue
        try:
            rows = await cold.export_partition(name)
            await drop_partition(name)
            archived += rows
            dropped += 1
            logger.info("Archived %s (%d rows) to cold storage, dropped partition", name, rows)
        except Exception:
            logger.exception("Cold export failed for %s - keeping partition", name)

    # tiny side tables: cheap DELETE is fine
    async with SessionLocal() as session:
        r3 = await session.execute(
            delete(OilSlick).where(OilSlick.ts < now - timedelta(days=180)))
        r4 = await session.execute(
            delete(DraughtChange).where(DraughtChange.ts < now - timedelta(days=120)))
        await session.commit()

    logger.info("Retention: %d partitions dropped (%d rows archived), %d kept; "
                "%d slicks, %d draught changes pruned",
                dropped, archived, kept, r3.rowcount or 0, r4.rowcount or 0)
