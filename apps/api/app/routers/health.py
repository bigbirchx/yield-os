from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db

router = APIRouter(prefix="/api", tags=["health"])

_HEARTBEAT_KEY = "yos:worker:heartbeat"
_HEARTBEAT_DOWN_AFTER_S = 300


async def _worker_status_summary() -> dict:
    """Quick worker liveness check from Redis heartbeat."""
    try:
        import redis.asyncio as aioredis
        from app.core.config import settings

        r = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=1.0,
        )
        heartbeat_raw = await r.get(_HEARTBEAT_KEY)
        await r.aclose()

        if heartbeat_raw is None:
            return {"worker": "down", "reason": "no heartbeat"}

        heartbeat_dt = datetime.fromisoformat(heartbeat_raw)
        age_s = (datetime.now(UTC) - heartbeat_dt).total_seconds()

        if age_s > _HEARTBEAT_DOWN_AFTER_S:
            return {"worker": "down", "heartbeat_age_seconds": round(age_s, 1)}
        if age_s > 120:
            return {"worker": "degraded", "heartbeat_age_seconds": round(age_s, 1)}
        return {"worker": "ok", "heartbeat_age_seconds": round(age_s, 1)}
    except Exception:
        return {"worker": "unknown", "reason": "redis_unavailable"}


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "error"

    worker_info = await _worker_status_summary()

    return {
        "status": "ok",
        "timestamp": datetime.now(UTC).isoformat(),
        "db": db_status,
        **worker_info,
    }
