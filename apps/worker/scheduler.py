"""
APScheduler setup for Yield Cockpit ingestion jobs.

Run directly:
    python -m apps.worker.scheduler

Or import build_scheduler() and call start() / shutdown() from a FastAPI lifespan.
"""

from __future__ import annotations

import asyncio

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from apps.worker.jobs.defillama_jobs import run_defillama_ingestion
from apps.worker.jobs.velo_jobs import run_velo_ingestion

log = structlog.get_logger(__name__)

VELO_INTERVAL_SECONDS = 300       # 5 minutes
DEFILLAMA_INTERVAL_SECONDS = 900  # 15 minutes


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_velo_ingestion,
        trigger=IntervalTrigger(seconds=VELO_INTERVAL_SECONDS),
        id="velo_ingestion",
        name="Velo derivatives ingestion (BTC/ETH/SOL)",
        replace_existing=True,
        misfire_grace_time=60,
    )
    scheduler.add_job(
        run_defillama_ingestion,
        trigger=IntervalTrigger(seconds=DEFILLAMA_INTERVAL_SECONDS),
        id="defillama_ingestion",
        name="DeFiLlama lending + staking ingestion",
        replace_existing=True,
        misfire_grace_time=120,
    )
    return scheduler


async def _run() -> None:
    scheduler = build_scheduler()
    scheduler.start()
    log.info("worker_scheduler_started", jobs=[j.id for j in scheduler.get_jobs()])
    try:
        # Fire both jobs immediately on startup before settling into schedule
        await run_velo_ingestion()
        await run_defillama_ingestion()
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        scheduler.shutdown()
        log.info("worker_scheduler_stopped")


if __name__ == "__main__":
    asyncio.run(_run())
