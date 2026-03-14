"""
Scheduled DeFiLlama ingestion jobs.
"""

from __future__ import annotations

import structlog

from app.core.database import AsyncSessionLocal
from app.services.defillama_ingestion import (
    SYMBOL_ALIASES,
    backfill_pool,
    ingest_all,
)
from app.repositories.lending import get_pool_ids_for_symbol

log = structlog.get_logger(__name__)


async def run_defillama_ingestion() -> None:
    """Ingest current lending and staking snapshots from DeFiLlama."""
    log.info("defillama_job_start")
    async with AsyncSessionLocal() as db:
        counts = await ingest_all(db)
    log.info("defillama_job_done", counts=counts)


async def run_defillama_backfill(symbols: list[str] | None = None) -> None:
    """
    Backfill historical lending data for all known pool IDs.

    Discovers pool IDs from the latest current snapshots, then fetches their
    full history from DeFiLlama /chart. Safe to re-run (rows are additive).
    """
    if symbols is None:
        symbols = list(SYMBOL_ALIASES.keys())

    log.info("defillama_backfill_start", symbols=symbols)
    async with AsyncSessionLocal() as db:
        pool_tuples = await get_pool_ids_for_symbol(db, symbols)

    log.info("defillama_backfill_pools_found", count=len(pool_tuples))

    for pool_id, symbol, protocol, chain in pool_tuples:
        if not pool_id:
            continue
        try:
            async with AsyncSessionLocal() as db:
                count = await backfill_pool(
                    db,
                    pool_id=pool_id,
                    symbol=symbol,
                    protocol=protocol,
                    chain=chain or "",
                )
            log.info(
                "defillama_backfill_pool_done",
                pool_id=pool_id,
                symbol=symbol,
                rows=count,
            )
        except Exception as exc:
            log.error(
                "defillama_backfill_pool_error",
                pool_id=pool_id,
                symbol=symbol,
                error=str(exc),
            )

    log.info("defillama_backfill_complete")
