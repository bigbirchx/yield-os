"""
GET /api/funding/history?symbol=BTC&exchange=binance&days=365&blend=false

Returns a time-series of daily annualized funding rates for one exchange or,
when blend=true, aligned blended series for all exchanges.
"""
from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.services.funding_service import get_blended_history, get_funding_history

router = APIRouter(prefix="/api/funding", tags=["funding"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class SeriesPoint(BaseModel):
    date: str
    value: float


class BlendSeries(BaseModel):
    equal_weighted: list[SeriesPoint]
    oi_weighted: list[SeriesPoint]
    volume_weighted: list[SeriesPoint]


class FundingHistoryOut(BaseModel):
    symbol: str
    exchange: str
    series: list[SeriesPoint]
    blend_series: BlendSeries | None = None


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/history", response_model=FundingHistoryOut)
async def funding_history(
    symbol: str = Query(default="BTC"),
    exchange: str = Query(
        default="binance",
        description="Exchange name: binance | okx | bybit | deribit",
    ),
    days: int = Query(
        default=365,
        ge=1,
        le=730,
        description="Number of days of history to return (max 730)",
    ),
    warmup_days: int = Query(
        default=0,
        ge=0,
        le=365,
        description=(
            "Extra days fetched before the display window to prime MA calculations. "
            "Returned data starts warmup_days before the display cutoff."
        ),
    ),
    blend: bool = Query(
        default=False,
        description="When true, return blended series for all available exchanges",
    ),
) -> FundingHistoryOut:
    """
    Daily annualized funding-rate history.

    - blend=false: raw daily series for the requested exchange
    - blend=true:  equal-weighted, OI-weighted, and volume-weighted blended
                   series across all available exchanges

    Use ``warmup_days`` to prepend extra data so client-side rolling MAs are
    fully primed (e.g. warmup_days=90 for a 90-day MA window).
    """
    sym = symbol.upper()

    if blend:
        blended = await get_blended_history(sym, days, warmup_days)

        def _pts(lst: list[dict]) -> list[SeriesPoint]:
            return [SeriesPoint(**p) for p in lst]

        primary = _pts(blended.get("equal_weighted", []))
        return FundingHistoryOut(
            symbol=sym,
            exchange="blended",
            series=primary,
            blend_series=BlendSeries(
                equal_weighted=primary,
                oi_weighted=_pts(blended.get("oi_weighted", [])),
                volume_weighted=_pts(blended.get("volume_weighted", [])),
            ),
        )

    df = await get_funding_history(sym, exchange, days, warmup_days)
    if df.empty:
        return FundingHistoryOut(symbol=sym, exchange=exchange, series=[])

    daily = df["annualized_funding_rate"].resample("D").mean().dropna()
    series = [
        SeriesPoint(date=d.strftime("%Y-%m-%d"), value=float(v))
        for d, v in daily.items()
    ]
    return FundingHistoryOut(symbol=sym, exchange=exchange, series=series)
