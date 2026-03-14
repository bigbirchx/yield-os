"""
Internal exchange data endpoints.

These endpoints are live-passthrough to the internal exchange connectors
(MongoDB-backed funding history, direct REST for current rates and RV).
They return data regardless of whether DB rows have accumulated yet.

Endpoints
---------
GET /api/derivatives/funding/history   — daily annualized funding history
GET /api/derivatives/funding/current   — live predicted funding rate per venue
GET /api/derivatives/rv               — realized volatility
"""
from __future__ import annotations

import asyncio
import math
from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.connectors.internal import exchange_client

router = APIRouter(prefix="/api/derivatives", tags=["derivatives-internal"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class FundingRateRow(BaseModel):
    snapshot_at: datetime
    symbol: str
    venue: str
    funding_rate: float | None
    funding_rate_annualized: float | None


class CurrentFundingRate(BaseModel):
    symbol: str
    venue: str
    funding_rate_annualized: float
    source: str = "internal"


class RVRow(BaseModel):
    timestamp: datetime
    c2c_vol_7: float | None = None
    c2c_vol_30: float | None = None
    c2c_vol_90: float | None = None
    parkinson_vol_7: float | None = None
    parkinson_vol_30: float | None = None
    parkinson_vol_90: float | None = None


class InternalAvailability(BaseModel):
    available: bool
    message: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/funding/history",
    response_model=list[FundingRateRow],
    summary="Internal funding-rate history",
)
async def funding_history(
    symbol: str = Query(default="BTC", description="Base currency, e.g. BTC"),
    exchange: str = Query(
        default="",
        description="Exchange filter: 'binance', 'okx', or '' for all",
    ),
    days: int = Query(default=365, ge=1, le=365),
) -> list[FundingRateRow]:
    """
    Daily annualized funding-rate history sourced from the internal MongoDB
    store (Binance + OKX, up to 3 years for actively tracked tokens).

    Returns an empty list if the internal APIs are unavailable.
    """
    df = await exchange_client.get_funding_rate_history(
        base_ccy=symbol.upper(),
        exchange=exchange.lower(),
        day_count=days,
    )
    if df.empty:
        return []

    rows: list[FundingRateRow] = []
    for _, r in df.iterrows():
        rows.append(
            FundingRateRow(
                snapshot_at=r["snapshot_at"],
                symbol=r["symbol"],
                venue=r["venue"],
                funding_rate=r.get("funding_rate"),
                funding_rate_annualized=r.get("funding_rate_annualized"),
            )
        )
    return rows


@router.get(
    "/funding/current",
    response_model=list[CurrentFundingRate],
    summary="Live predicted funding rate from internal connectors",
)
async def funding_current(
    symbol: str = Query(default="BTC"),
) -> list[CurrentFundingRate]:
    """
    Current predicted / next-period annualized funding rate from Binance
    and OKX via the internal exchange connectors.
    """
    binance_rate, okx_rate = await asyncio.gather(
        exchange_client.get_current_funding_rate(symbol.upper(), "binance"),
        exchange_client.get_current_funding_rate(symbol.upper(), "okx"),
    )
    return [
        CurrentFundingRate(
            symbol=symbol.upper(),
            venue="binance",
            funding_rate_annualized=binance_rate,
        ),
        CurrentFundingRate(
            symbol=symbol.upper(),
            venue="okx",
            funding_rate_annualized=okx_rate,
        ),
    ]


@router.get(
    "/rv",
    response_model=list[RVRow],
    summary="Realized volatility from internal mark-price OHLC",
)
async def realized_vol(
    symbol: str = Query(default="BTC"),
) -> list[RVRow]:
    """
    Realized volatility (close-to-close and Parkinson) at 7 / 30 / 90-day
    windows, derived from perp mark-price OHLC via the internal connectors.
    """
    df = await exchange_client.get_realized_vol(
        base_ccy=symbol.upper(),
        day_counts=[7, 30, 90],
    )
    if df.empty:
        return []

    rows: list[RVRow] = []
    for _, r in df.iterrows():
        rows.append(
            RVRow(
                timestamp=r.get("timestamp"),
                c2c_vol_7=_opt(r, "c2c_vol_7"),
                c2c_vol_30=_opt(r, "c2c_vol_30"),
                c2c_vol_90=_opt(r, "c2c_vol_90"),
                parkinson_vol_7=_opt(r, "parkinson_vol_7"),
                parkinson_vol_30=_opt(r, "parkinson_vol_30"),
                parkinson_vol_90=_opt(r, "parkinson_vol_90"),
            )
        )
    return rows


def _opt(row: object, col: str) -> float | None:
    val = getattr(row, col, None)
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None
