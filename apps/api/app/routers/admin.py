"""
Admin endpoints for manual data operations.

These endpoints are unauthenticated — suitable for internal/local use only.
Do not expose to the public internet without adding auth.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
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
# Redis key constants (must match apps/worker/config.py)
# ---------------------------------------------------------------------------

_HEARTBEAT_KEY = "yos:worker:heartbeat"
_HEALTH_HASH_KEY = "yos:worker:health"
_SUMMARY_KEY = "yos:worker:summary"

_HEARTBEAT_HEALTHY_MAX_S = 120   # < 2 min → healthy
_HEARTBEAT_DOWN_AFTER_S = 300    # > 5 min → down
_FAILURE_THRESHOLD = 3           # consecutive failures for "degraded"


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class IngestResult(BaseModel):
    triggered_at: datetime
    defillama: dict[str, str | int]
    aave: dict[str, str | int]
    morpho: dict[str, str | int]
    kamino: dict[str, str | int]


class BackfillResult(BaseModel):
    triggered_at: datetime
    pools_found: int
    rows_written: int
    errors: list[str]


class SourceStatus(BaseModel):
    key: str
    label: str
    status: Literal["fresh", "stale", "missing"]
    last_updated: datetime | None
    row_count: int
    stale_threshold_minutes: int
    populates: list[str]


class JobHealth(BaseModel):
    last_run: str | None = None
    last_success: str | None = None
    last_status: Literal["success", "failure", "unknown"] = "unknown"
    consecutive_failures: int = 0
    duration_seconds: float | None = None
    detail: dict[str, Any] | None = None


class WorkerHealthResponse(BaseModel):
    worker_status: Literal["healthy", "degraded", "down"]
    last_heartbeat: str | None = None
    heartbeat_age_seconds: float | None = None
    jobs: dict[str, JobHealth]
    total_opportunities: int | None = None
    by_venue: dict[str, int] | None = None
    by_chain: dict[str, int] | None = None


# ---------------------------------------------------------------------------
# Sources metadata — what each connector feeds and how fresh it should be
# ---------------------------------------------------------------------------

_SOURCES: list[dict] = [
    {
        "key": "defillama",
        "label": "DeFiLlama",
        "table": "lending_market_snapshots",
        "where": None,
        "stale_minutes": 20,
        "populates": [
            "Overview → Lending rates (all symbols)",
            "Asset cockpit → Supply & borrow APYs",
            "Asset cockpit → Rate history chart",
            "Borrow-demand explainer",
            "Route optimizer → available liquidity",
        ],
    },
    {
        "key": "aave",
        "label": "Aave v3",
        "table": "protocol_risk_params_snapshots",
        "where": "protocol LIKE 'aave-v3%'",
        "stale_minutes": 60,
        "populates": [
            "Asset cockpit → Risk params (LTV, liq. threshold, caps)",
            "LTV matrix → Aave rows",
            "Route optimizer → collateral safety check",
        ],
    },
    {
        "key": "morpho",
        "label": "Morpho Blue",
        "table": "protocol_risk_params_snapshots",
        "where": "protocol = 'morpho-blue'",
        "stale_minutes": 60,
        "populates": [
            "Asset cockpit → Risk params (LLTV, market APYs)",
            "LTV matrix → Morpho rows",
        ],
    },
    {
        "key": "kamino",
        "label": "Kamino",
        "table": "protocol_risk_params_snapshots",
        "where": "protocol = 'kamino'",
        "stale_minutes": 60,
        "populates": [
            "Asset cockpit → Solana risk params",
            "LTV matrix → Kamino rows",
        ],
    },
    {
        "key": "velo",
        "label": "Velo",
        "table": "derivatives_snapshots",
        "where": "(raw_payload->>'source') IS DISTINCT FROM 'internal'",
        "stale_minutes": 10,
        "populates": [
            "Overview → Derivatives (funding, OI, basis, volume)",
            "Asset cockpit → Derivatives table",
            "Asset cockpit → Funding rate history chart",
        ],
    },
    {
        "key": "internal_binance",
        "label": "Binance (internal)",
        "table": "derivatives_snapshots",
        "where": "venue = 'binance'",
        "stale_minutes": 10,
        "populates": [
            "GET /api/derivatives/funding/history (365-day)",
            "GET /api/derivatives/funding/current",
            "GET /api/derivatives/rv → realized vol",
        ],
    },
    {
        "key": "internal_okx",
        "label": "OKX (internal)",
        "table": "derivatives_snapshots",
        "where": "venue = 'okx'",
        "stale_minutes": 10,
        "populates": [
            "GET /api/derivatives/funding/history (365-day)",
            "GET /api/derivatives/funding/current",
        ],
    },
    {
        "key": "coingecko_market",
        "label": "CoinGecko (market)",
        "table": "market_reference_snapshots",
        "where": None,
        "stale_minutes": 20,
        "populates": [
            "Asset cockpit → Spot price + market cap context",
            "Asset cockpit → 24h volume",
            "Overview → Global market context card",
            "GET /api/reference/assets",
        ],
    },
    {
        "key": "coingecko_history",
        "label": "CoinGecko (history)",
        "table": "market_reference_history",
        "where": None,
        "stale_minutes": 1500,  # daily backfill; stale after 25h
        "populates": [
            "Asset cockpit → CoinGecko price history chart",
            "GET /api/reference/history/{symbol}",
        ],
    },
]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/sources", response_model=list[SourceStatus])
async def list_sources(db: AsyncSession = Depends(get_db)) -> list[SourceStatus]:
    """
    Return freshness status for every configured data source.

    Status values:
    - **fresh**   — data updated within the stale threshold
    - **stale**   — data exists but is older than the threshold
    - **missing** — no rows found in the database
    """
    now = datetime.now(UTC)
    results: list[SourceStatus] = []

    for src in _SOURCES:
        table = src["table"]
        where_clause = f"WHERE {src['where']}" if src["where"] else ""

        row = (
            await db.execute(
                text(
                    f"SELECT count(*) AS n, max(snapshot_at) AS latest "
                    f"FROM {table} {where_clause}"
                )
            )
        ).one()

        row_count: int = int(row.n or 0)
        last_updated: datetime | None = row.latest

        if row_count == 0 or last_updated is None:
            status: Literal["fresh", "stale", "missing"] = "missing"
        elif now - last_updated > timedelta(minutes=src["stale_minutes"]):
            status = "stale"
        else:
            status = "fresh"

        results.append(
            SourceStatus(
                key=src["key"],
                label=src["label"],
                status=status,
                last_updated=last_updated,
                row_count=row_count,
                stale_threshold_minutes=src["stale_minutes"],
                populates=src["populates"],
            )
        )

    return results


async def _get_redis_conn():
    """Return an async Redis connection, or None if unavailable."""
    try:
        import redis.asyncio as aioredis
        from app.core.config import settings as app_settings

        r = aioredis.from_url(
            app_settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2.0,
        )
        await r.ping()
        return r
    except Exception:
        return None


@router.get("/worker-health", response_model=WorkerHealthResponse)
async def worker_health(
    db: AsyncSession = Depends(get_db),
) -> WorkerHealthResponse:
    """
    Return worker process health derived from Redis heartbeat and job metrics.

    Status logic:
    - **healthy** — heartbeat < 2 min old, no jobs have > 3 consecutive failures
    - **degraded** — heartbeat is recent but some jobs are failing
    - **down** — heartbeat > 5 min old or Redis unreachable
    """
    r = await _get_redis_conn()
    if r is None:
        return WorkerHealthResponse(
            worker_status="down",
            jobs={},
        )

    try:
        now = datetime.now(UTC)

        # ── Heartbeat ───────────────────────────────────────────────────
        heartbeat_raw = await r.get(_HEARTBEAT_KEY)
        heartbeat_age_s: float | None = None
        if heartbeat_raw:
            heartbeat_dt = datetime.fromisoformat(heartbeat_raw)
            heartbeat_age_s = (now - heartbeat_dt).total_seconds()

        # ── Per-job health ──────────────────────────────────────────────
        job_entries_raw: dict[str, str] = await r.hgetall(_HEALTH_HASH_KEY)
        jobs: dict[str, JobHealth] = {}
        max_consecutive_failures = 0

        for job_name, raw in job_entries_raw.items():
            entry = json.loads(raw)
            consecutive = entry.get("consecutive_failures", 0)
            max_consecutive_failures = max(max_consecutive_failures, consecutive)
            jobs[job_name] = JobHealth(
                last_run=entry.get("last_run"),
                last_success=entry.get("last_success"),
                last_status="success" if entry.get("success") else "failure",
                consecutive_failures=consecutive,
                duration_seconds=entry.get("duration_s"),
                detail=entry.get("detail"),
            )

        # ── Summary (written by health_report job) ──────────────────────
        summary_raw = await r.get(_SUMMARY_KEY)
        total_opportunities: int | None = None
        by_venue: dict[str, int] | None = None
        by_chain: dict[str, int] | None = None

        if summary_raw:
            summary = json.loads(summary_raw)
            total_opportunities = summary.get("total_opportunities")
            by_venue = summary.get("by_venue")

        # by_chain isn't in the summary yet — query the DB directly
        from app.models.opportunity import MarketOpportunityRow
        from sqlalchemy import func, select

        chain_rows = (await db.execute(
            select(
                MarketOpportunityRow.chain,
                func.count().label("n"),
            ).group_by(MarketOpportunityRow.chain)
        )).all()
        if chain_rows:
            by_chain = {r.chain: r.n for r in chain_rows}

        # If we don't have a summary yet, also get total + by_venue from DB
        if total_opportunities is None:
            total_opportunities = (await db.execute(
                select(func.count()).select_from(MarketOpportunityRow)
            )).scalar() or 0
        if by_venue is None:
            venue_rows = (await db.execute(
                select(
                    MarketOpportunityRow.venue,
                    func.count().label("n"),
                ).group_by(MarketOpportunityRow.venue)
            )).all()
            by_venue = {r.venue: r.n for r in venue_rows}

        # ── Determine overall status ────────────────────────────────────
        if heartbeat_raw is None or (heartbeat_age_s and heartbeat_age_s > _HEARTBEAT_DOWN_AFTER_S):
            worker_status: Literal["healthy", "degraded", "down"] = "down"
        elif max_consecutive_failures >= _FAILURE_THRESHOLD:
            worker_status = "degraded"
        elif heartbeat_age_s and heartbeat_age_s > _HEARTBEAT_HEALTHY_MAX_S:
            worker_status = "degraded"
        else:
            worker_status = "healthy"

        return WorkerHealthResponse(
            worker_status=worker_status,
            last_heartbeat=heartbeat_raw,
            heartbeat_age_seconds=round(heartbeat_age_s, 1) if heartbeat_age_s is not None else None,
            jobs=jobs,
            total_opportunities=total_opportunities,
            by_venue=by_venue,
            by_chain=by_chain,
        )
    finally:
        await r.aclose()


async def _publish_trigger(job: str = "full_ingestion") -> bool:
    """Try to publish a trigger to the worker via Redis pub/sub.

    Returns True if the message was published, False if Redis is unavailable.
    """
    try:
        import json

        import redis.asyncio as aioredis
        from app.core.config import settings as app_settings

        r = aioredis.from_url(
            app_settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2.0,
        )
        await r.publish(
            "yos:worker:trigger",
            json.dumps({"job": job}),
        )
        await r.aclose()
        return True
    except Exception as exc:
        log.warning("redis_trigger_failed", error=str(exc))
        return False


@router.post("/ingest", response_model=IngestResult)
async def trigger_ingest(db: AsyncSession = Depends(get_db)) -> IngestResult:
    """
    Trigger an immediate ingestion run.

    Publishes to the worker process via Redis pub/sub.  If Redis is
    unavailable, falls back to running ingestion inline.
    """
    now = datetime.now(UTC)

    enqueued = await _publish_trigger("full_ingestion")
    if enqueued:
        log.info("admin_ingest_enqueued")
        return IngestResult(
            triggered_at=now,
            defillama={"status": "enqueued_to_worker"},
            aave={"status": "enqueued_to_worker"},
            morpho={"status": "enqueued_to_worker"},
            kamino={"status": "enqueued_to_worker"},
        )

    # Fallback: run inline if worker/Redis unavailable
    log.info("admin_ingest_inline_fallback")

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

    # CoinGecko market snapshots — best-effort, works with free tier
    from app.services.coingecko_ingestion import ingest_market_snapshots as cg_ingest
    try:
        await cg_ingest(db)
    except Exception as exc:
        log.warning("admin_ingest_coingecko_error", error=str(exc))

    # Internal exchange connectors
    try:
        from app.services.internal_ingestion import ingest_all as internal_ingest_all
        await internal_ingest_all(db)
    except Exception as exc:
        log.warning("admin_ingest_internal_error", error=str(exc))

    # Protocol-native borrow rates
    try:
        from app.services.lending_rate_ingestion import ingest_all_borrow_rates
        await ingest_all_borrow_rates(db)
    except Exception as exc:
        log.error("admin_ingest_borrow_rates_error", error=str(exc))

    # Unified opportunity ingestion
    try:
        from app.services.opportunity_ingestion import OpportunityIngestionService
        svc = OpportunityIngestionService()
        await svc.run_full_ingestion(db)
    except Exception as exc:
        log.error("admin_ingest_opportunities_error", error=str(exc))

    log.info("admin_ingest_complete_inline")
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
