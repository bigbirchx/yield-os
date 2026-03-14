import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.routers import derivatives, health

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
app.include_router(derivatives.router)

_scheduler: AsyncIOScheduler | None = None


@app.on_event("startup")
async def on_startup() -> None:
    log.info("api_starting", env=settings.app_env)

    if settings.velo_api_key:
        from app.core.database import AsyncSessionLocal
        from app.services.velo_ingestion import ingest_all

        global _scheduler
        _scheduler = AsyncIOScheduler()
        _scheduler.add_job(
            _velo_job,
            trigger=IntervalTrigger(seconds=300),
            id="velo_ingestion",
            replace_existing=True,
            misfire_grace_time=60,
        )
        _scheduler.start()
        log.info("velo_scheduler_started")
    else:
        log.warning("velo_scheduler_skipped", reason="VELO_API_KEY not set")


async def _velo_job() -> None:
    from app.core.database import AsyncSessionLocal
    from app.services.velo_ingestion import ingest_all

    async with AsyncSessionLocal() as db:
        counts = await ingest_all(db)
    log.info("velo_scheduled_run", counts=counts)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    log.info("api_shutdown")
