"""
Worker job definitions.

Each job is an async function that opens its own DB session, runs the
ingestion logic, and logs results.  Jobs are registered by the scheduler
in :mod:`apps.worker.main`.

All jobs follow the same pattern:
  1. Log start
  2. Open a fresh AsyncSession
  3. Call the shared ingestion service / adapter
  4. Update health counters in Redis
  5. Log completion or error
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from typing import Any

import structlog

from app.core.database import AsyncSessionLocal
try:
    from apps.worker.config import worker_settings
except ModuleNotFoundError:
    from config import worker_settings

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Redis health helper
# ---------------------------------------------------------------------------

_redis: Any = None


async def _get_redis() -> Any:
    """Return a Redis connection, or None if unavailable."""
    global _redis
    if _redis is not None:
        try:
            await _redis.ping()
            return _redis
        except Exception:
            _redis = None
    try:
        import redis.asyncio as aioredis
        from app.core.config import settings

        _redis = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2.0,
        )
        await _redis.ping()
        return _redis
    except Exception:
        return None


async def _update_health(
    job_name: str,
    *,
    success: bool,
    duration_s: float,
    detail: dict | None = None,
) -> None:
    """Write per-job health info to Redis hash."""
    r = await _get_redis()
    if r is None:
        return
    try:
        key = worker_settings.redis_health_key
        now = datetime.now(UTC).isoformat()
        entry = {
            "last_run": now,
            "success": success,
            "duration_s": round(duration_s, 2),
        }
        if success:
            entry["last_success"] = now
        if detail:
            entry["detail"] = detail

        # Track consecutive failures
        existing_raw = await r.hget(key, job_name)
        failures = 0
        if existing_raw:
            existing = json.loads(existing_raw)
            if not success:
                failures = existing.get("consecutive_failures", 0) + 1
            if success and "last_success" not in entry:
                entry["last_success"] = existing.get("last_success")
        entry["consecutive_failures"] = failures if not success else 0

        await r.hset(key, job_name, json.dumps(entry))
    except Exception as exc:
        log.debug("health_update_failed", job=job_name, error=str(exc))


async def _run_with_timeout(coro, timeout_s: int, job_name: str) -> Any:
    """Run a coroutine with a timeout. Returns the result or raises."""
    try:
        return await asyncio.wait_for(coro, timeout=timeout_s)
    except asyncio.TimeoutError:
        log.error("job_timeout", job=job_name, timeout_s=timeout_s)
        raise


# ---------------------------------------------------------------------------
# Job: Funding Rates (1 min)
# ---------------------------------------------------------------------------


async def ingest_funding_rates() -> None:
    """Fetch perpetual funding rates from Binance, OKX, Bybit, Deribit."""
    job = "ingest_funding_rates"
    t0 = time.monotonic()
    try:
        from app.connectors.funding_rate import (
            BinanceFundingRateAdapter,
            BybitFundingRateAdapter,
            DeribitFundingRateAdapter,
            OkxFundingRateAdapter,
        )
        from app.services.opportunity_ingestion import OpportunityIngestionService

        adapters = [
            BinanceFundingRateAdapter(),
            OkxFundingRateAdapter(),
            BybitFundingRateAdapter(),
            DeribitFundingRateAdapter(),
        ]

        svc = OpportunityIngestionService()
        all_opps = []
        errors = []

        results = await asyncio.gather(
            *[a.safe_fetch_opportunities() for a in adapters],
            return_exceptions=True,
        )
        for adapter, result in zip(adapters, results):
            if isinstance(result, Exception):
                errors.append(f"{adapter.protocol_slug}: {result}")
            else:
                all_opps.extend(result)

        async with AsyncSessionLocal() as db:
            from app.services.opportunity_ingestion import (
                _upsert_opportunities,
            )

            upserted = await _upsert_opportunities(db, all_opps)
            await db.commit()

        dur = time.monotonic() - t0
        log.info(job, upserted=upserted, errors=len(errors), duration_s=round(dur, 2))
        await _update_health(
            job, success=True, duration_s=dur,
            detail={"upserted": upserted, "errors": errors},
        )
    except Exception as exc:
        dur = time.monotonic() - t0
        log.exception(job + "_failed", error=str(exc))
        await _update_health(job, success=False, duration_s=dur)


# ---------------------------------------------------------------------------
# Job: Lending Protocols (5 min)
# ---------------------------------------------------------------------------


async def ingest_lending_protocols() -> None:
    """Fetch lending opportunities from Aave, Morpho, Compound, Euler, Spark, Kamino, Jupiter, JustLend."""
    job = "ingest_lending_protocols"
    t0 = time.monotonic()
    try:
        from app.connectors.aave_v3 import AaveV3Adapter
        from app.connectors.compound_v3 import CompoundV3Adapter
        from app.connectors.euler_v2 import EulerV2Adapter
        from app.connectors.jupiter import JupiterAdapter
        from app.connectors.justlend import JustLendAdapter
        from app.connectors.kamino import KaminoAdapter
        from app.connectors.katana import KatanaAdapter
        from app.connectors.morpho import MorphoAdapter
        from app.connectors.spark import SparkAdapter
        from app.services.opportunity_ingestion import (
            _insert_snapshots,
            _upsert_opportunities,
        )

        adapters = [
            AaveV3Adapter(),
            MorphoAdapter(),
            CompoundV3Adapter(),
            EulerV2Adapter(),
            SparkAdapter(),
            KaminoAdapter(),
            JupiterAdapter(),
            JustLendAdapter(),
            KatanaAdapter(),
        ]

        all_opps = []
        by_venue = {}
        errors = []
        now = datetime.now(UTC)

        results = await asyncio.gather(
            *[a.safe_fetch_opportunities() for a in adapters],
            return_exceptions=True,
        )
        for adapter, result in zip(adapters, results):
            venue = adapter.venue.value
            if isinstance(result, Exception):
                errors.append(f"{venue}: {result}")
                by_venue[venue] = 0
            else:
                all_opps.extend(result)
                by_venue[venue] = len(result)

        async with AsyncSessionLocal() as db:
            upserted = await _upsert_opportunities(db, all_opps)
            snapshots = await _insert_snapshots(db, all_opps, now)
            await db.commit()

        dur = time.monotonic() - t0
        log.info(
            job, upserted=upserted, snapshots=snapshots,
            by_venue=by_venue, errors=len(errors), duration_s=round(dur, 2),
        )
        await _update_health(
            job, success=True, duration_s=dur,
            detail={"upserted": upserted, "by_venue": by_venue, "errors": errors},
        )
    except Exception as exc:
        dur = time.monotonic() - t0
        log.exception(job + "_failed", error=str(exc))
        await _update_health(job, success=False, duration_s=dur)


# ---------------------------------------------------------------------------
# Job: Derivatives / Basis + Pendle (5 min)
# ---------------------------------------------------------------------------


async def ingest_derivatives() -> None:
    """Fetch basis trade and Pendle PT/YT opportunities."""
    job = "ingest_derivatives"
    t0 = time.monotonic()
    try:
        from app.connectors.basis_trade import BasisTradeAdapter
        from app.connectors.pendle import PendleAdapter
        from app.services.opportunity_ingestion import (
            _insert_snapshots,
            _upsert_opportunities,
        )

        adapters = [BasisTradeAdapter(), PendleAdapter()]
        all_opps = []
        by_venue = {}
        now = datetime.now(UTC)

        results = await asyncio.gather(
            *[a.safe_fetch_opportunities() for a in adapters],
            return_exceptions=True,
        )
        for adapter, result in zip(adapters, results):
            venue = adapter.venue.value
            if isinstance(result, Exception):
                by_venue[venue] = 0
            else:
                all_opps.extend(result)
                by_venue[venue] = len(result)

        async with AsyncSessionLocal() as db:
            upserted = await _upsert_opportunities(db, all_opps)
            await _insert_snapshots(db, all_opps, now)
            await db.commit()

        dur = time.monotonic() - t0
        log.info(job, upserted=upserted, by_venue=by_venue, duration_s=round(dur, 2))
        await _update_health(job, success=True, duration_s=dur, detail=by_venue)
    except Exception as exc:
        dur = time.monotonic() - t0
        log.exception(job + "_failed", error=str(exc))
        await _update_health(job, success=False, duration_s=dur)


# ---------------------------------------------------------------------------
# Job: Staking / Savings (15 min)
# ---------------------------------------------------------------------------


async def ingest_staking_savings() -> None:
    """Fetch staking (Lido, EtherFi) and savings (Sky/Maker DSR/SSR)."""
    job = "ingest_staking_savings"
    t0 = time.monotonic()
    try:
        from app.connectors.etherfi import EtherFiAdapter
        from app.connectors.lido import LidoAdapter
        from app.connectors.sky import SkyAdapter
        from app.services.opportunity_ingestion import (
            _upsert_opportunities,
        )

        adapters = [LidoAdapter(), EtherFiAdapter(), SkyAdapter()]
        all_opps = []
        by_venue = {}

        results = await asyncio.gather(
            *[a.safe_fetch_opportunities() for a in adapters],
            return_exceptions=True,
        )
        for adapter, result in zip(adapters, results):
            venue = adapter.venue.value
            if isinstance(result, Exception):
                by_venue[venue] = 0
            else:
                all_opps.extend(result)
                by_venue[venue] = len(result)

        async with AsyncSessionLocal() as db:
            upserted = await _upsert_opportunities(db, all_opps)
            await db.commit()

        dur = time.monotonic() - t0
        log.info(job, upserted=upserted, by_venue=by_venue, duration_s=round(dur, 2))
        await _update_health(job, success=True, duration_s=dur, detail=by_venue)
    except Exception as exc:
        dur = time.monotonic() - t0
        log.exception(job + "_failed", error=str(exc))
        await _update_health(job, success=False, duration_s=dur)


# ---------------------------------------------------------------------------
# Job: CEX Earn (15 min)
# ---------------------------------------------------------------------------


async def ingest_cex_earn() -> None:
    """Fetch CeFi earn rates (Binance, OKX flexible/locked)."""
    job = "ingest_cex_earn"
    t0 = time.monotonic()
    try:
        from app.connectors.cex_earn import BinanceEarnAdapter, OkxEarnAdapter
        from app.services.opportunity_ingestion import (
            _upsert_opportunities,
        )

        adapters = [BinanceEarnAdapter(), OkxEarnAdapter()]
        all_opps = []
        by_venue = {}

        results = await asyncio.gather(
            *[a.safe_fetch_opportunities() for a in adapters],
            return_exceptions=True,
        )
        for adapter, result in zip(adapters, results):
            venue = adapter.venue.value
            if isinstance(result, Exception):
                by_venue[venue] = 0
            else:
                all_opps.extend(result)
                by_venue[venue] = len(result)

        async with AsyncSessionLocal() as db:
            upserted = await _upsert_opportunities(db, all_opps)
            await db.commit()

        dur = time.monotonic() - t0
        log.info(job, upserted=upserted, by_venue=by_venue, duration_s=round(dur, 2))
        await _update_health(job, success=True, duration_s=dur, detail=by_venue)
    except Exception as exc:
        dur = time.monotonic() - t0
        log.exception(job + "_failed", error=str(exc))
        await _update_health(job, success=False, duration_s=dur)


# ---------------------------------------------------------------------------
# Job: DeFiLlama fallback (10 min)
# ---------------------------------------------------------------------------


async def ingest_defillama() -> None:
    """Run the DeFiLlama lending + staking ingestion (legacy pipeline)."""
    job = "ingest_defillama"
    t0 = time.monotonic()
    try:
        from app.services.defillama_ingestion import ingest_all

        async with AsyncSessionLocal() as db:
            counts = await ingest_all(db)

        dur = time.monotonic() - t0
        log.info(job, counts=counts, duration_s=round(dur, 2))
        await _update_health(job, success=True, duration_s=dur, detail=counts)
    except Exception as exc:
        dur = time.monotonic() - t0
        log.exception(job + "_failed", error=str(exc))
        await _update_health(job, success=False, duration_s=dur)


# ---------------------------------------------------------------------------
# Job: DeFiLlama Extended (4h)
# ---------------------------------------------------------------------------


async def ingest_defillama_extended() -> None:
    """Run the extended DeFiLlama pipeline (protocols, chains, stablecoins, market context)."""
    job = "ingest_defillama_extended"
    t0 = time.monotonic()
    try:
        from app.services.defillama_ingestion import ingest_all_extended

        async with AsyncSessionLocal() as db:
            counts = await ingest_all_extended(db)

        dur = time.monotonic() - t0
        log.info(job, counts=counts, duration_s=round(dur, 2))
        await _update_health(job, success=True, duration_s=dur, detail=counts)
    except Exception as exc:
        dur = time.monotonic() - t0
        log.exception(job + "_failed", error=str(exc))
        await _update_health(job, success=False, duration_s=dur)


# ---------------------------------------------------------------------------
# Job: Snapshot Rates (5 min)
# ---------------------------------------------------------------------------


async def snapshot_rates() -> None:
    """Take a rate snapshot of all current opportunities."""
    job = "snapshot_rates"
    t0 = time.monotonic()
    try:
        from sqlalchemy import select

        from app.models.opportunity import MarketOpportunityRow
        from app.services.opportunity_ingestion import (
            _insert_snapshots,
        )
        from opportunity_schema import MarketOpportunity

        now = datetime.now(UTC)

        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(MarketOpportunityRow)
            )).scalars().all()

            if not rows:
                dur = time.monotonic() - t0
                log.info(job, snapshots=0, duration_s=round(dur, 2))
                await _update_health(job, success=True, duration_s=dur, detail={"snapshots": 0})
                return

            # Build lightweight snapshot dicts directly from rows
            from app.services.opportunity_ingestion import (
                _opportunity_to_snapshot,
            )

            snapshot_rows = []
            for row in rows:
                snapshot_rows.append({
                    "opportunity_id": row.opportunity_id,
                    "snapshot_at": now,
                    "total_apy_pct": row.total_apy_pct,
                    "base_apy_pct": row.base_apy_pct,
                    "total_supplied": row.total_supplied,
                    "total_supplied_usd": row.total_supplied_usd,
                    "total_borrowed": row.total_borrowed,
                    "total_borrowed_usd": row.total_borrowed_usd,
                    "utilization_rate_pct": (
                        row.liquidity.get("utilization_rate_pct")
                        if isinstance(row.liquidity, dict) else None
                    ),
                    "tvl_usd": row.tvl_usd,
                })

            from sqlalchemy.dialects.postgresql import insert as pg_insert
            from app.models.opportunity import MarketOpportunitySnapshotRow

            for i in range(0, len(snapshot_rows), 500):
                chunk = snapshot_rows[i : i + 500]
                stmt = pg_insert(MarketOpportunitySnapshotRow).values(chunk)
                await db.execute(stmt)
            await db.commit()

        dur = time.monotonic() - t0
        log.info(job, snapshots=len(snapshot_rows), duration_s=round(dur, 2))
        await _update_health(
            job, success=True, duration_s=dur,
            detail={"snapshots": len(snapshot_rows)},
        )
    except Exception as exc:
        dur = time.monotonic() - t0
        log.exception(job + "_failed", error=str(exc))
        await _update_health(job, success=False, duration_s=dur)


# ---------------------------------------------------------------------------
# Job: Prune Stale Opportunities (1h)
# ---------------------------------------------------------------------------


async def prune_stale_opportunities() -> None:
    """Remove opportunities not updated within max_age_hours."""
    job = "prune_stale_opportunities"
    t0 = time.monotonic()
    try:
        from app.services.opportunity_ingestion import OpportunityIngestionService

        svc = OpportunityIngestionService()
        async with AsyncSessionLocal() as db:
            deleted = await svc.prune_stale(
                db, max_age_hours=worker_settings.prune_max_age_hours,
            )

        dur = time.monotonic() - t0
        if deleted:
            log.info(job, deleted=deleted, duration_s=round(dur, 2))
        await _update_health(
            job, success=True, duration_s=dur, detail={"deleted": deleted},
        )
    except Exception as exc:
        dur = time.monotonic() - t0
        log.exception(job + "_failed", error=str(exc))
        await _update_health(job, success=False, duration_s=dur)


# ---------------------------------------------------------------------------
# Job: Health Report (5 min)
# ---------------------------------------------------------------------------


async def health_report() -> None:
    """Write a summary to Redis so the API can expose worker health."""
    job = "health_report"
    t0 = time.monotonic()
    try:
        from sqlalchemy import func, select
        from app.models.opportunity import MarketOpportunityRow

        async with AsyncSessionLocal() as db:
            total = (await db.execute(
                select(func.count()).select_from(MarketOpportunityRow)
            )).scalar() or 0

            venue_rows = (await db.execute(
                select(
                    MarketOpportunityRow.venue,
                    func.count().label("n"),
                ).group_by(MarketOpportunityRow.venue)
            )).all()

            last_update_rows = (await db.execute(
                select(
                    MarketOpportunityRow.venue,
                    func.max(MarketOpportunityRow.last_updated_at).label("last"),
                ).group_by(MarketOpportunityRow.venue)
            )).all()

        r = await _get_redis()
        if r is None:
            return

        summary = {
            "timestamp": datetime.now(UTC).isoformat(),
            "total_opportunities": total,
            "by_venue": {row.venue: row.n for row in venue_rows},
            "last_update_by_venue": {
                row.venue: row.last.isoformat() if row.last else None
                for row in last_update_rows
            },
            "worker_pid": __import__("os").getpid(),
        }
        await r.set(
            f"{worker_settings.redis_prefix}:summary",
            json.dumps(summary),
            ex=600,  # expire after 10 min (stale if worker is down)
        )

        dur = time.monotonic() - t0
        await _update_health(
            job, success=True, duration_s=dur, detail={"total": total},
        )
    except Exception as exc:
        dur = time.monotonic() - t0
        log.exception(job + "_failed", error=str(exc))
        await _update_health(job, success=False, duration_s=dur)


# ---------------------------------------------------------------------------
# Legacy jobs (migrated from API in-process scheduler)
# ---------------------------------------------------------------------------


async def legacy_velo_ingestion() -> None:
    """Velo derivatives ingestion (BTC/ETH/SOL)."""
    job = "legacy_velo"
    t0 = time.monotonic()
    try:
        from app.core.config import settings
        if not settings.velo_api_key:
            return

        from app.services.velo_ingestion import ingest_all

        async with AsyncSessionLocal() as db:
            counts = await ingest_all(db)

        dur = time.monotonic() - t0
        log.info(job, counts=counts, duration_s=round(dur, 2))
        await _update_health(job, success=True, duration_s=dur)
    except Exception as exc:
        dur = time.monotonic() - t0
        log.exception(job + "_failed", error=str(exc))
        await _update_health(job, success=False, duration_s=dur)


async def legacy_internal_exchange() -> None:
    """Binance + OKX internal exchange connectors."""
    job = "legacy_internal"
    t0 = time.monotonic()
    try:
        from app.services.internal_ingestion import ingest_all

        async with AsyncSessionLocal() as db:
            counts = await ingest_all(db)

        dur = time.monotonic() - t0
        log.info(job, counts=counts, duration_s=round(dur, 2))
        await _update_health(job, success=True, duration_s=dur)
    except Exception as exc:
        dur = time.monotonic() - t0
        log.exception(job + "_failed", error=str(exc))
        await _update_health(job, success=False, duration_s=dur)


async def legacy_coingecko() -> None:
    """CoinGecko market snapshot + API usage."""
    job = "legacy_coingecko"
    t0 = time.monotonic()
    try:
        from app.services.coingecko_ingestion import (
            ingest_api_usage,
            ingest_market_snapshots,
        )

        async with AsyncSessionLocal() as db:
            count = await ingest_market_snapshots(db)

        async with AsyncSessionLocal() as db:
            await ingest_api_usage(db)

        dur = time.monotonic() - t0
        log.info(job, snapshots=count, duration_s=round(dur, 2))
        await _update_health(job, success=True, duration_s=dur)
    except Exception as exc:
        dur = time.monotonic() - t0
        log.exception(job + "_failed", error=str(exc))
        await _update_health(job, success=False, duration_s=dur)


async def legacy_borrow_rates() -> None:
    """Protocol-native borrow rates (Aave, Kamino, Morpho Blue)."""
    job = "legacy_borrow_rates"
    t0 = time.monotonic()
    try:
        from app.services.lending_rate_ingestion import ingest_all_borrow_rates

        async with AsyncSessionLocal() as db:
            counts = await ingest_all_borrow_rates(db)

        dur = time.monotonic() - t0
        log.info(job, counts=counts, duration_s=round(dur, 2))
        await _update_health(job, success=True, duration_s=dur)
    except Exception as exc:
        dur = time.monotonic() - t0
        log.exception(job + "_failed", error=str(exc))
        await _update_health(job, success=False, duration_s=dur)


# ---------------------------------------------------------------------------
# Job: Token Universe Refresh (24 hours)
# ---------------------------------------------------------------------------


async def refresh_token_universe() -> None:
    """Refresh top-500 token universe from CoinGecko, update Redis + DB."""
    job = "refresh_token_universe"
    t0 = time.monotonic()
    try:
        from app.services.token_universe import get_price_service, get_token_universe
        from asset_registry import set_global_fallback_lookup

        universe = get_token_universe()
        counts = await universe.refresh()

        # Re-wire the global normalizer fallback with fresh data
        def _universe_fallback(raw_symbol: str) -> str | None:
            asset = universe.get_token(raw_symbol)
            return asset.canonical_id if asset else None

        set_global_fallback_lookup(_universe_fallback)

        # Update Redis price cache
        price_svc = get_price_service()
        await price_svc.update_from_market_data(universe.get_market_data())

        # Persist full universe to DB (upsert)
        async with AsyncSessionLocal() as db:
            sync_counts = await universe.sync_to_db(db)
        counts.update(sync_counts)

        dur = time.monotonic() - t0
        log.info(job, counts=counts, duration_s=round(dur, 2))
        await _update_health(job, success=True, duration_s=dur, detail=counts)
    except Exception as exc:
        dur = time.monotonic() - t0
        log.exception(job + "_failed", error=str(exc))
        await _update_health(job, success=False, duration_s=dur)


# ---------------------------------------------------------------------------
# Job: Price Refresh (5 minutes)
# ---------------------------------------------------------------------------


async def refresh_prices() -> None:
    """Fetch latest prices from CoinGecko and update Redis + DB price columns."""
    job = "refresh_prices"
    t0 = time.monotonic()
    try:
        from app.connectors.coingecko_client import get_client
        from app.services.token_universe import get_price_service, get_token_universe

        client = get_client()
        all_coins: list[dict] = []

        for page in (1, 2):
            try:
                data = await client.coins_markets(per_page=250, page=page)
                if data:
                    all_coins.extend(data)
            except Exception as exc:
                log.warning("refresh_prices_fetch_error", page=page, error=str(exc))

        if not all_coins:
            raise RuntimeError("No price data returned from CoinGecko")

        # Update Redis cache
        price_svc = get_price_service()
        cached = await price_svc.update_from_market_data(all_coins)

        # Update price columns in DB (targeted — only price/rank fields)
        universe = get_token_universe()
        async with AsyncSessionLocal() as db:
            updated = await universe.update_prices_in_db(db, all_coins)

        dur = time.monotonic() - t0
        detail = {"cached": cached, "db_rows_updated": updated}
        log.info(job, duration_s=round(dur, 2), **detail)
        await _update_health(job, success=True, duration_s=dur, detail=detail)
    except Exception as exc:
        dur = time.monotonic() - t0
        log.exception(job + "_failed", error=str(exc))
        await _update_health(job, success=False, duration_s=dur)


# ---------------------------------------------------------------------------
# On-demand trigger (called via Redis pub/sub from API)
# ---------------------------------------------------------------------------


async def trigger_full_ingestion() -> dict:
    """Run a complete ingestion across all adapter groups.

    Called by the Redis trigger listener when the API posts a refresh request.
    """
    log.info("trigger_full_ingestion_start")
    t0 = time.monotonic()

    results = await asyncio.gather(
        ingest_lending_protocols(),
        ingest_derivatives(),
        ingest_staking_savings(),
        ingest_cex_earn(),
        ingest_funding_rates(),
        return_exceptions=True,
    )

    errors = [str(r) for r in results if isinstance(r, Exception)]
    dur = time.monotonic() - t0
    log.info("trigger_full_ingestion_done", errors=len(errors), duration_s=round(dur, 2))

    return {"duration_seconds": round(dur, 2), "errors": errors}
