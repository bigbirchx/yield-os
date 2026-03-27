"""
GET /api/funding/snapshot?symbol=BTC

Returns live funding rates, OI, volume, moving averages and blended metrics
for all configured exchanges, plus a Coinglass cross-check strip.
"""
from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.services.funding_service import ExchangeData, get_funding_snapshot

router = APIRouter(prefix="/api/funding", tags=["funding"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class ExchangeOut(BaseModel):
    live_apr: float | None = None
    last_apr: float | None = None
    funding_interval_hours: float | None = None
    oi_coin: float | None = None
    oi_usd: float | None = None
    volume_coin_24h: float | None = None
    volume_usd_24h: float | None = None
    ma_7d_apr: float | None = None
    ma_30d_apr: float | None = None

    model_config = {"from_attributes": True}


class BlendedOut(BaseModel):
    equal_weighted_apr: float | None = None
    oi_weighted_apr: float | None = None
    volume_weighted_apr: float | None = None


class CoinglassOut(BaseModel):
    binance_apr: float | None = None
    okx_apr: float | None = None
    bybit_apr: float | None = None


class FundingSnapshotOut(BaseModel):
    symbol: str
    as_of: str
    exchanges: dict[str, ExchangeOut]
    blended: BlendedOut
    coinglass: CoinglassOut


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/snapshot", response_model=FundingSnapshotOut)
async def funding_snapshot(
    symbol: str = Query(default="BTC", description="Base currency (BTC, ETH, SOL)"),
) -> FundingSnapshotOut:
    """
    Live perpetual funding-rate snapshot for a single symbol across all exchanges.

    Sources: Binance (internal), OKX (internal), Bybit (REST), Deribit (REST),
             Bullish (gated by credentials), Coinglass (cross-check).

    All APR values are annualized (e.g. 0.15 = 15% p.a.).
    Deribit is USD-settled — note this when comparing with USDT-settled venues.
    """
    snap = await get_funding_snapshot(symbol.upper())

    return FundingSnapshotOut(
        symbol=snap.symbol,
        as_of=snap.as_of,
        exchanges={
            name: ExchangeOut(
                live_apr=ex.live_apr,
                last_apr=ex.last_apr,
                funding_interval_hours=ex.funding_interval_hours,
                oi_coin=ex.oi_coin,
                oi_usd=ex.oi_usd,
                volume_coin_24h=ex.volume_coin_24h,
                volume_usd_24h=ex.volume_usd_24h,
                ma_7d_apr=ex.ma_7d_apr,
                ma_30d_apr=ex.ma_30d_apr,
            )
            for name, ex in snap.exchanges.items()
        },
        blended=BlendedOut(**snap.blended),
        coinglass=CoinglassOut(**snap.coinglass) if snap.coinglass else CoinglassOut(),
    )
