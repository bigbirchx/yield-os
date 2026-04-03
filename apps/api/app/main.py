import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.routers import (
    admin,
    assets,
    basis,
    book,
    borrow_demand,
    defillama,
    derivatives,
    funding_history,
    funding_snapshot,
    health,
    internal_derivatives,
    lending,
    opportunities,
    reference,
    risk,
    route_optimizer,
    staking,
    tokens,
    yield_optimizer,
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
app.include_router(assets.router)
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
app.include_router(yield_optimizer.router)
app.include_router(defillama.router)
app.include_router(opportunities.router)
app.include_router(tokens.router)
app.include_router(book.router)


@app.on_event("startup")
async def on_startup() -> None:
    log.info("api_starting", env=settings.app_env)

    # Register all protocol adapters (needed for inline refresh fallback)
    try:
        from app.services.opportunity_ingestion import register_adapters
        register_adapters()
    except Exception:
        log.exception("opportunity_adapter_registration_failed")

    # Sync in-memory asset registry to DB (idempotent upsert)
    try:
        from app.core.database import AsyncSessionLocal
        from app.services.asset_registry_sync import sync_asset_registry

        async with AsyncSessionLocal() as db:
            counts = await sync_asset_registry(db)
        log.info("asset_registry_sync_complete", counts=counts)
    except Exception:
        log.exception("asset_registry_sync_failed")

    # Refresh the top-500 token universe and wire normalizer fallback
    try:
        from app.services.token_universe import (
            get_price_service,
            get_token_universe,
        )

        universe = get_token_universe()
        counts = await universe.refresh()
        log.info("token_universe_ready", **counts)

        # Wire normalizer fallback so unknown symbols resolve via universe
        from asset_registry import set_global_fallback_lookup

        def _universe_fallback(raw_symbol: str) -> str | None:
            asset = universe.get_token(raw_symbol)
            return asset.canonical_id if asset else None

        set_global_fallback_lookup(_universe_fallback)

        # Populate price cache from the market data we just fetched
        price_svc = get_price_service()
        await price_svc.update_from_market_data(universe.get_market_data())

        # Persist token universe to DB so API endpoints can query it
        try:
            from app.core.database import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                sync_counts = await universe.sync_to_db(db)
            log.info("token_universe_db_sync_complete", **sync_counts)
        except Exception:
            log.exception("token_universe_db_sync_failed")
    except Exception:
        log.exception("token_universe_init_failed")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    log.info("api_shutdown")
