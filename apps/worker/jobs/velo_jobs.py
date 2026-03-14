"""
Scheduled Velo ingestion jobs.

Called by APScheduler every 5 minutes. Each job opens its own DB session
so it is safe to run while the API is serving requests.
"""

from __future__ import annotations

import structlog

from app.core.database import AsyncSessionLocal
from app.services.velo_ingestion import TRACKED_COINS, ingest_coin

log = structlog.get_logger(__name__)


async def run_velo_ingestion() -> None:
    """Ingest latest derivatives data for all tracked coins from Velo."""
    log.info("velo_job_start", coins=TRACKED_COINS)
    async with AsyncSessionLocal() as db:
        for coin in TRACKED_COINS:
            try:
                count = await ingest_coin(coin, db)
                log.info("velo_job_coin_done", coin=coin, rows=count)
            except Exception as exc:
                log.error("velo_job_coin_error", coin=coin, error=str(exc))
    log.info("velo_job_done")
