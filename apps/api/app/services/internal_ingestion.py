"""
Scheduled ingestion service — internal exchange connectors.

Fetches the current funding rate and market metrics (OI, volume) from
Binance and OKX for each tracked coin and persists a DerivativesSnapshot row.

This runs every 5 minutes alongside the Velo scheduler so the DB accumulates
a live funding-rate time series from internal sources even when Velo is absent.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog

from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.internal import exchange_client
from app.connectors.internal.path_setup import _HAS_APIS
from app.models.snapshot import DerivativesSnapshot

log = structlog.get_logger(__name__)

TRACKED_COINS = ["BTC", "ETH", "SOL"]
TRACKED_EXCHANGES = ["binance", "okx"]


async def ingest_all(db: AsyncSession) -> dict[str, int]:
    """
    Snapshot current funding rate + market metrics for all tracked coins
    across Binance and OKX and persist to derivatives_snapshots.

    Returns a dict of {venue: rows_inserted}.
    """
    # Continue even when reference codebase is unavailable — REST fallbacks
    # in exchange_client will still provide live rates from Binance/OKX.

    now = datetime.now(UTC)
    counts: dict[str, int] = {}

    tasks = [
        _ingest_coin_exchange(db, coin, exch, now)
        for coin in TRACKED_COINS
        for exch in TRACKED_EXCHANGES
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for coin, exch, result in zip(
        [c for c in TRACKED_COINS for _ in TRACKED_EXCHANGES],
        [e for _ in TRACKED_COINS for e in TRACKED_EXCHANGES],
        results,
    ):
        if isinstance(result, Exception):
            log.exception(
                "internal_ingest_error", coin=coin, exchange=exch, error=str(result)
            )
        elif result:
            counts[exch] = counts.get(exch, 0) + 1

    await db.commit()
    return counts


async def _ingest_coin_exchange(
    db: AsyncSession,
    base_ccy: str,
    exchange: str,
    snapshot_at: datetime,
) -> bool:
    funding_ann = await exchange_client.get_current_funding_rate(base_ccy, exchange)
    metrics = await exchange_client.get_market_metrics(base_ccy, exchange)

    oi_usd = _safe_float(
        metrics.get("perpetual_open_interest_USD") or metrics.get("perpetual_open_interest")
    )
    perp_vol = _safe_float(
        metrics.get("perpetual_volume_24h_USD") or metrics.get("perpetual_volume_24h")
    )
    spot_vol = _safe_float(
        metrics.get("spot_volume_24h_USD") or metrics.get("spot_volume_24h")
    )

    # 8-hour funding rate  ≈ annualized / (365 * 3)
    funding_per_period = funding_ann / (365 * 3) if funding_ann else None

    row = DerivativesSnapshot(
        symbol=base_ccy.upper(),
        venue=exchange,
        funding_rate=funding_per_period,
        open_interest_usd=oi_usd,
        perp_volume_usd=perp_vol,
        spot_volume_usd=spot_vol,
        raw_payload={
            "funding_annualized": funding_ann,
            "source": "internal",
            **metrics,
        },
        snapshot_at=snapshot_at,
    )
    db.add(row)
    return True


def _safe_float(val: object) -> float | None:
    try:
        return float(val) if val is not None else None  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
