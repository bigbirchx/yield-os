"""
Velo ingestion service.

Calls VeloClient, normalizes each VeloSnapshot into a DerivativesSnapshot row,
and persists to Postgres. Raw payloads are preserved verbatim.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.velo_client import VeloClient, VeloSnapshot
from app.core.config import settings
from app.models.snapshot import DerivativesSnapshot

log = structlog.get_logger(__name__)

TRACKED_COINS = ["BTC", "ETH", "SOL"]


def _normalize(snapshot: VeloSnapshot, now: datetime) -> DerivativesSnapshot:
    """Map a VeloSnapshot to the ORM model. Raw payloads are stored as-is."""
    raw = {
        "funding": snapshot.raw_funding,
        "open_interest": snapshot.raw_oi,
        "market_summary": snapshot.raw_summary,
    }
    return DerivativesSnapshot(
        symbol=snapshot.coin,
        venue=snapshot.venue,
        funding_rate=snapshot.funding_rate,
        open_interest_usd=snapshot.open_interest_usd,
        basis_annualized=snapshot.basis_annualized,
        mark_price=snapshot.mark_price,
        index_price=snapshot.index_price,
        spot_volume_usd=snapshot.spot_volume_usd,
        perp_volume_usd=snapshot.perp_volume_usd,
        raw_payload=raw,
        snapshot_at=snapshot.fetched_at,
    )


async def ingest_coin(coin: str, db: AsyncSession) -> int:
    """
    Fetch all venues for one coin from Velo and persist to the DB.
    Returns the number of rows written.
    """
    async with VeloClient(api_key=settings.velo_api_key) as client:
        snapshots = await client.fetch_snapshots(coin)

    now = datetime.now(UTC)
    rows = [_normalize(s, now) for s in snapshots]

    db.add_all(rows)
    await db.commit()

    log.info("velo_ingestion_done", coin=coin, rows=len(rows))
    return len(rows)


async def ingest_all(db: AsyncSession) -> dict[str, int]:
    """Ingest BTC, ETH, and SOL sequentially. Returns counts per coin."""
    results: dict[str, int] = {}
    for coin in TRACKED_COINS:
        try:
            results[coin] = await ingest_coin(coin, db)
        except Exception as exc:
            log.error("velo_ingestion_error", coin=coin, error=str(exc))
            results[coin] = 0
    return results
