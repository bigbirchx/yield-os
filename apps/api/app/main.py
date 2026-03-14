import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.routers import health

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


@app.on_event("startup")
async def on_startup():
    log.info("api_starting", env=settings.app_env)


@app.on_event("shutdown")
async def on_shutdown():
    log.info("api_shutdown")
