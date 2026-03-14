"""
Admin endpoints for manual data operations.

These endpoints are unauthenticated — suitable for internal/local use only.
Do not expose to the public internet without adding auth.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.repositories.lending import get_pool_ids_for_symbol
from app.services.defillama_ingestion import (
    TRACKED_LENDING_SYMBOLS,
    backfill_pool,
    ingest_all as defillama_ingest_all,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class IngestResult(BaseModel):
    triggered_at: datetime
    defillama: dict[str, int]
    aave: dict[str, str | int]
    morpho: dict[str, str | int]
    kamino: dict[str, str | int]


class BackfillResult(BaseModel):
    triggered_at: datetime
    pools_found: int
    rows_written: int
    errors: list[str]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/ingest", response_model=IngestResult)
async def trigger_ingest(db: AsyncSession = Depends(get_db)) -> IngestResult:
    """
    Trigger an immediate DeFiLlama + Morpho + Kamino ingestion run.

    DeFiLlama is always available (no key required).
    Morpho and Kamino use public APIs.
    Aave requires AAVE_SUBGRAPH_KEY in the environment.
    """
    now = datetime.now(UTC)

    # DeFiLlama (lending + staking)
    defillama_counts = await defillama_ingest_all(db)

    # Aave, Morpho, Kamino risk params — each is best-effort and independently isolated
    from app.services.risk_ingestion import ingest_aave, ingest_morpho, ingest_kamino

    aave_counts: dict[str, str | int] = {}
    try:
        aave_counts["rows"] = await ingest_aave(db)
    except Exception as exc:
        log.error("admin_ingest_aave_error", error=str(exc))
        aave_counts["error"] = str(exc)

    morpho_counts: dict[str, str | int] = {}
    try:
        morpho_counts["rows"] = await ingest_morpho(db)
    except Exception as exc:
        log.error("admin_ingest_morpho_error", error=str(exc))
        morpho_counts["error"] = str(exc)

    kamino_counts: dict[str, str | int] = {}
    try:
        kamino_counts["rows"] = await ingest_kamino(db)
    except Exception as exc:
        log.error("admin_ingest_kamino_error", error=str(exc))
        kamino_counts["error"] = str(exc)

    log.info("admin_ingest_complete", defillama=defillama_counts, aave=aave_counts)
    return IngestResult(
        triggered_at=now,
        defillama=defillama_counts,
        aave=aave_counts,
        morpho=morpho_counts,
        kamino=kamino_counts,
    )


@router.post("/backfill", response_model=BackfillResult)
async def trigger_backfill(
    days: int = Query(default=90, ge=1, le=365),
    concurrency: int = Query(default=4, ge=1, le=10),
    db: AsyncSession = Depends(get_db),
) -> BackfillResult:
    """
    Backfill up to `days` days of daily lending-rate history for every tracked
    pool that has been ingested at least once.

    Step 1 — run a live ingest first so pool_ids are populated.
    Step 2 — for each distinct pool_id, fetch /chart/{pool_id} from DeFiLlama
             and insert one lending_market_snapshot row per day.

    Use `days=90` for a quick 3-month history or `days=365` for a full year.
    Runs up to `concurrency` pools in parallel (default 4).
    """
    now = datetime.now(UTC)
    errors: list[str] = []

    # Ensure we have at least one snapshot so pool_ids exist
    log.info("admin_backfill_pre_ingest")
    await defillama_ingest_all(db)

    # Discover all pool_ids from the now-populated snapshots
    all_symbols = list(TRACKED_LENDING_SYMBOLS)
    pool_tuples = await get_pool_ids_for_symbol(db, all_symbols)

    if not pool_tuples:
        log.warning("admin_backfill_no_pools")
        return BackfillResult(
            triggered_at=now,
            pools_found=0,
            rows_written=0,
            errors=["No pools found — ingest may have returned zero rows"],
        )

    log.info("admin_backfill_pools_found", count=len(pool_tuples))

    # Backfill pools in batches of `concurrency`
    total_rows = 0
    sem = asyncio.Semaphore(concurrency)

    async def _backfill_one(pool_id: str, symbol: str, protocol: str, chain: str) -> int:
        async with sem:
            try:
                from app.core.database import AsyncSessionLocal

                async with AsyncSessionLocal() as session:
                    return await backfill_pool(session, pool_id, symbol, protocol, chain or "")
            except Exception as exc:
                msg = f"{pool_id}: {exc}"
                log.error("admin_backfill_pool_error", pool_id=pool_id, error=str(exc))
                errors.append(msg)
                return 0

    tasks = [
        _backfill_one(pool_id, symbol, protocol, chain or "")
        for pool_id, symbol, protocol, chain in pool_tuples
        if pool_id
    ]
    results = await asyncio.gather(*tasks)
    total_rows = sum(results)

    log.info("admin_backfill_complete", pools=len(tasks), rows=total_rows, errors=len(errors))
    return BackfillResult(
        triggered_at=now,
        pools_found=len(tasks),
        rows_written=total_rows,
        errors=errors,
    )
