"""
Standalone worker process.

Runs APScheduler with a Redis job store for persistence.  Listens on a
Redis pub/sub channel so the API can trigger on-demand jobs.

Usage::

    python -m apps.worker.main          # from repo root
    # or inside Docker:
    python main.py
"""
from __future__ import annotations

import asyncio
import json
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path

import structlog

# ---------------------------------------------------------------------------
# Ensure repo packages are importable.
#
# Inside Docker:  /api-src has app.*, /worker-src has config.py/jobs.py
# From repo root: apps/api has app.*, apps/worker has config.py/jobs.py
# ---------------------------------------------------------------------------
_worker_dir = Path(__file__).resolve().parent
_repo_root = _worker_dir.parent.parent
_api_root = _repo_root / "apps" / "api"

# Docker layout: /api-src for app.*, /worker-src for worker siblings
for p in [
    str(_worker_dir),       # so `import config`, `import jobs` works
    str(_api_root),         # so `from app.core...` works (repo root run)
    "/api-src",             # so `from app.core...` works (Docker)
    str(_repo_root),        # so `from apps.worker...` works (repo root run)
]:
    if p not in sys.path:
        sys.path.insert(0, p)

from apscheduler.jobstores.redis import RedisJobStore  # noqa: E402
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # noqa: E402
from apscheduler.triggers.interval import IntervalTrigger  # noqa: E402

from app.core.config import settings  # noqa: E402

from config import worker_settings  # noqa: E402
from jobs import (  # noqa: E402
    health_report,
    ingest_cex_earn,
    ingest_defillama,
    ingest_defillama_extended,
    ingest_derivatives,
    ingest_funding_rates,
    ingest_lending_protocols,
    ingest_staking_savings,
    legacy_borrow_rates,
    legacy_coingecko,
    legacy_internal_exchange,
    legacy_velo_ingestion,
    prune_stale_opportunities,
    refresh_prices,
    refresh_token_universe,
    snapshot_rates,
    trigger_full_ingestion,
)

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(
        getattr(__import__("logging"), settings.log_level, 20)
    )
)

log = structlog.get_logger("worker")

# ---------------------------------------------------------------------------
# Redis pub/sub listener
# ---------------------------------------------------------------------------

_TRIGGER_HANDLERS: dict[str, object] = {
    "full_ingestion": trigger_full_ingestion,
    "lending_protocols": ingest_lending_protocols,
    "funding_rates": ingest_funding_rates,
    "derivatives": ingest_derivatives,
    "staking_savings": ingest_staking_savings,
    "cex_earn": ingest_cex_earn,
    "defillama": ingest_defillama,
    "snapshot_rates": snapshot_rates,
    "token_universe": refresh_token_universe,
    "refresh_prices": refresh_prices,
}


async def _pubsub_listener() -> None:
    """Listen on the Redis trigger channel and dispatch jobs on demand."""
    import redis.asyncio as aioredis

    r = aioredis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=5.0,
    )
    pubsub = r.pubsub()
    await pubsub.subscribe(worker_settings.redis_trigger_channel)
    log.info("pubsub_listener_started", channel=worker_settings.redis_trigger_channel)

    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                payload = json.loads(message["data"])
                job_name = payload.get("job", "full_ingestion")
            except (json.JSONDecodeError, TypeError):
                job_name = str(message["data"])

            handler = _TRIGGER_HANDLERS.get(job_name)
            if handler is None:
                log.warning("pubsub_unknown_job", job=job_name)
                continue

            log.info("pubsub_trigger_received", job=job_name)
            asyncio.create_task(handler())
    except asyncio.CancelledError:
        pass
    finally:
        await pubsub.unsubscribe(worker_settings.redis_trigger_channel)
        await r.aclose()


# ---------------------------------------------------------------------------
# Scheduler setup
# ---------------------------------------------------------------------------

_JOB_SCHEDULE = [
    # (id, func, interval_seconds, timeout_seconds)
    ("ingest_funding_rates",      ingest_funding_rates,      worker_settings.interval_funding_rates,      worker_settings.timeout_funding_rates),
    ("ingest_lending_protocols",  ingest_lending_protocols,  worker_settings.interval_lending_protocols,  worker_settings.timeout_lending_protocols),
    ("ingest_derivatives",        ingest_derivatives,        worker_settings.interval_derivatives,        worker_settings.timeout_derivatives),
    ("ingest_staking_savings",    ingest_staking_savings,    worker_settings.interval_staking_savings,    worker_settings.timeout_staking_savings),
    ("ingest_cex_earn",           ingest_cex_earn,           worker_settings.interval_cex_earn,           worker_settings.timeout_cex_earn),
    ("ingest_defillama",          ingest_defillama,          worker_settings.interval_defillama,          worker_settings.timeout_defillama),
    ("ingest_defillama_extended", ingest_defillama_extended, worker_settings.interval_defillama_extended, worker_settings.timeout_defillama_extended),
    ("snapshot_rates",            snapshot_rates,            worker_settings.interval_snapshot_rates,      worker_settings.timeout_snapshot_rates),
    ("prune_stale_opportunities", prune_stale_opportunities, worker_settings.interval_prune_stale,        worker_settings.timeout_prune_stale),
    ("health_report",             health_report,             worker_settings.interval_health_report,      worker_settings.timeout_health_report),
    # Legacy jobs migrated from API
    ("legacy_velo",               legacy_velo_ingestion,     worker_settings.interval_legacy_velo,        60),
    ("legacy_internal",           legacy_internal_exchange,  worker_settings.interval_legacy_internal,    60),
    ("legacy_coingecko",          legacy_coingecko,          worker_settings.interval_legacy_coingecko,   60),
    ("legacy_borrow_rates",       legacy_borrow_rates,       worker_settings.interval_legacy_borrow_rates, 120),
    ("refresh_token_universe",    refresh_token_universe,    worker_settings.interval_token_universe,      worker_settings.timeout_token_universe),
    ("refresh_prices",            refresh_prices,            worker_settings.interval_refresh_prices,      worker_settings.timeout_refresh_prices),
]


def _build_scheduler() -> AsyncIOScheduler:
    """Create the scheduler with a Redis job store and register all jobs."""
    # Parse Redis URL for host/port/db
    from urllib.parse import urlparse

    parsed = urlparse(settings.redis_url)
    redis_host = parsed.hostname or "localhost"
    redis_port = parsed.port or 6379

    jobstore = RedisJobStore(
        host=redis_host,
        port=redis_port,
        db=worker_settings.redis_jobstore_db,
    )

    scheduler = AsyncIOScheduler(
        jobstores={"default": jobstore},
        job_defaults={
            "max_instances": 1,           # distributed lock: only one instance runs
            "coalesce": True,             # collapse missed fires into one
            "misfire_grace_time": 120,    # tolerate up to 2 min delay
        },
    )

    for job_id, func, interval, _timeout in _JOB_SCHEDULE:
        scheduler.add_job(
            func,
            trigger=IntervalTrigger(seconds=interval),
            id=job_id,
            replace_existing=True,
        )

    return scheduler


# ---------------------------------------------------------------------------
# Heartbeat (Docker healthcheck reads this key)
# ---------------------------------------------------------------------------

_HEARTBEAT_KEY = "yos:worker:heartbeat"
_HEARTBEAT_INTERVAL = 30  # seconds
_HEARTBEAT_TTL = 90       # expire if worker dies (3× interval)


async def _heartbeat_loop() -> None:
    """Write a timestamp to Redis every 30s so Docker can health-check us."""
    import redis.asyncio as aioredis

    try:
        r = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2.0,
        )
        while True:
            try:
                await r.set(
                    _HEARTBEAT_KEY,
                    datetime.now(UTC).isoformat(),
                    ex=_HEARTBEAT_TTL,
                )
            except Exception as exc:
                log.debug("heartbeat_write_failed", error=str(exc))
            await asyncio.sleep(_HEARTBEAT_INTERVAL)
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------


async def _startup() -> None:
    """Register protocol adapters and sync asset registry."""
    try:
        from app.services.opportunity_ingestion import register_adapters
        register_adapters()
    except Exception:
        log.exception("adapter_registration_failed")

    try:
        from app.core.database import AsyncSessionLocal
        from app.services.asset_registry_sync import sync_asset_registry

        async with AsyncSessionLocal() as db:
            counts = await sync_asset_registry(db)
        log.info("asset_registry_sync_complete", counts=counts)
    except Exception:
        log.exception("asset_registry_sync_failed")

    # Refresh token universe and wire normalizer fallback
    try:
        from app.services.token_universe import get_price_service, get_token_universe
        from asset_registry import set_global_fallback_lookup

        universe = get_token_universe()
        counts = await universe.refresh()
        log.info("token_universe_ready", **counts)

        def _universe_fallback(raw_symbol: str) -> str | None:
            asset = universe.get_token(raw_symbol)
            return asset.canonical_id if asset else None

        set_global_fallback_lookup(_universe_fallback)

        price_svc = get_price_service()
        await price_svc.update_from_market_data(universe.get_market_data())
    except Exception:
        log.exception("token_universe_init_failed")


async def _main() -> None:
    log.info(
        "worker_starting",
        redis=settings.redis_url,
        jobs=len(_JOB_SCHEDULE),
    )

    await _startup()

    scheduler = _build_scheduler()
    scheduler.start()
    log.info("scheduler_started", jobs=[j[0] for j in _JOB_SCHEDULE])

    # Start pub/sub listener as a background task
    pubsub_task = asyncio.create_task(_pubsub_listener())

    # Start heartbeat writer (every 30s, checked by Docker healthcheck)
    heartbeat_task = asyncio.create_task(_heartbeat_loop())

    # Wait for shutdown signal
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    await stop_event.wait()

    # Graceful shutdown
    log.info("worker_shutting_down")
    for task in (pubsub_task, heartbeat_task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    scheduler.shutdown(wait=False)
    log.info("worker_stopped")


if __name__ == "__main__":
    asyncio.run(_main())
