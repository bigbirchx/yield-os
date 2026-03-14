import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.routers import (
    admin,
    basis,
    borrow_demand,
    derivatives,
    funding_history,
    funding_snapshot,
    health,
    internal_derivatives,
    lending,
    reference,
    risk,
    route_optimizer,
    staking,
)

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(
        getattr(__import__("logging"), settings.log_level, 20)
    )
)

log = structlog.get_logger()

app = FastAPI(
    title="Yield Cockpit API",
    description="Institutional crypto yield monitoring backend",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(admin.router)
app.include_router(basis.router)
app.include_router(derivatives.router)
app.include_router(internal_derivatives.router)
app.include_router(funding_snapshot.router)
app.include_router(funding_history.router)
app.include_router(reference.router)
app.include_router(lending.router)
app.include_router(risk.router)
app.include_router(staking.router)
app.include_router(borrow_demand.router)
app.include_router(route_optimizer.router)

_scheduler: AsyncIOScheduler | None = None


@app.on_event("startup")
async def on_startup() -> None:
    log.info("api_starting", env=settings.app_env)

    global _scheduler
    _scheduler = AsyncIOScheduler()

    if settings.velo_api_key:
        _scheduler.add_job(
            _velo_job,
            trigger=IntervalTrigger(seconds=300),
            id="velo_ingestion",
            replace_existing=True,
            misfire_grace_time=60,
        )
        log.info("velo_scheduler_registered")
    else:
        log.warning("velo_scheduler_skipped", reason="VELO_API_KEY not set")

    # DeFiLlama is public; schedule regardless of API key
    _scheduler.add_job(
        _defillama_job,
        trigger=IntervalTrigger(seconds=900),  # 15 min
        id="defillama_ingestion",
        replace_existing=True,
        misfire_grace_time=120,
    )
    log.info("defillama_scheduler_registered")

    # Internal exchange connectors (Binance + OKX via internal libs).
    # Runs every 5 min alongside Velo; gracefully skips when paths unavailable.
    _scheduler.add_job(
        _internal_job,
        trigger=IntervalTrigger(seconds=300),
        id="internal_exchange_ingestion",
        replace_existing=True,
        misfire_grace_time=60,
    )
    log.info("internal_exchange_scheduler_registered")

    # CoinGecko market reference snapshots — 15 min (works with free tier too).
    _scheduler.add_job(
        _coingecko_snapshot_job,
        trigger=IntervalTrigger(seconds=900),
        id="coingecko_market_snapshot",
        replace_existing=True,
        misfire_grace_time=120,
    )
    log.info("coingecko_snapshot_scheduler_registered")

    # CoinGecko API usage — 30 min (Pro only; no-op if no key).
    _scheduler.add_job(
        _coingecko_usage_job,
        trigger=IntervalTrigger(seconds=1800),
        id="coingecko_api_usage",
        replace_existing=True,
        misfire_grace_time=120,
    )
    log.info("coingecko_usage_scheduler_registered")

    _scheduler.start()


async def _velo_job() -> None:
    from app.core.database import AsyncSessionLocal
    from app.services.velo_ingestion import ingest_all

    async with AsyncSessionLocal() as db:
        counts = await ingest_all(db)
    log.info("velo_scheduled_run", counts=counts)


async def _defillama_job() -> None:
    from app.core.database import AsyncSessionLocal
    from app.services.defillama_ingestion import ingest_all

    async with AsyncSessionLocal() as db:
        counts = await ingest_all(db)
    log.info("defillama_scheduled_run", counts=counts)


async def _internal_job() -> None:
    from app.core.database import AsyncSessionLocal
    from app.services.internal_ingestion import ingest_all

    async with AsyncSessionLocal() as db:
        counts = await ingest_all(db)
    log.info("internal_exchange_scheduled_run", counts=counts)


async def _coingecko_snapshot_job() -> None:
    from app.core.database import AsyncSessionLocal
    from app.services.coingecko_ingestion import ingest_market_snapshots

    async with AsyncSessionLocal() as db:
        count = await ingest_market_snapshots(db)
    log.info("coingecko_snapshot_run", inserted=count)


async def _coingecko_usage_job() -> None:
    from app.core.database import AsyncSessionLocal
    from app.services.coingecko_ingestion import ingest_api_usage

    async with AsyncSessionLocal() as db:
        stored = await ingest_api_usage(db)
    log.info("coingecko_usage_run", stored=stored)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    log.info("api_shutdown")
