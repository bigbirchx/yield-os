"""
GET /api/basis/snapshot?symbol=BTC
GET /api/basis/history?symbol=BTC&venue=deribit&contract=BTC-28MAR25&days=89
"""
from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.services.basis_service import BasisRow, get_basis_history, get_basis_snapshot

router = APIRouter(prefix="/api/basis", tags=["basis"])


# ---- Response schemas --------------------------------------------------------

class TermStructureRow(BaseModel):
    venue: str
    contract: str
    expiry: str
    days_to_expiry: int
    futures_price: float
    index_price: float
    basis_usd: float
    basis_pct_ann: float | None = None
    oi_coin: float | None = None
    oi_usd: float | None = None
    volume_24h_usd: float | None = None


class BasisSnapshotOut(BaseModel):
    symbol: str
    as_of: str
    term_structure: list[TermStructureRow]


class BasisHistoryPoint(BaseModel):
    timestamp: str
    basis_usd: float | None = None
    basis_pct_ann: float | None = None
    futures_price: float | None = None
    index_price: float | None = None
    days_to_expiry: float | None = None


class BasisHistoryOut(BaseModel):
    symbol: str
    venue: str
    contract: str
    expiry: str | None
    series: list[BasisHistoryPoint]


# ---- Endpoints ---------------------------------------------------------------

@router.get("/snapshot", response_model=BasisSnapshotOut)
async def basis_snapshot(
    symbol: str = Query(default="BTC", description="Base currency (BTC, ETH)"),
) -> BasisSnapshotOut:
    """
    Current dated futures term structure across Deribit, Binance, OKX, Bybit, CME.

    Returns one row per active contract sorted by days_to_expiry ascending.
    Perpetuals are excluded (they belong in /api/funding/snapshot).
    DTE = 0 contracts are excluded.
    basis_pct_ann is annualised decimal (0.05 = 5%).
    CME is omitted if Amberdata subscription is unavailable.
    """
    from datetime import UTC, datetime

    rows = await get_basis_snapshot(symbol.upper())
    return BasisSnapshotOut(
        symbol=symbol.upper(),
        as_of=datetime.now(UTC).isoformat(),
        term_structure=[
            TermStructureRow(
                venue=r.venue,
                contract=r.contract,
                expiry=r.expiry,
                days_to_expiry=r.days_to_expiry,
                futures_price=r.futures_price,
                index_price=r.index_price,
                basis_usd=r.basis_usd,
                basis_pct_ann=r.basis_pct_ann,
                oi_coin=r.oi_coin,
                oi_usd=r.oi_usd,
                volume_24h_usd=r.volume_24h_usd,
            )
            for r in rows
        ],
    )


@router.get("/history", response_model=BasisHistoryOut)
async def basis_history(
    symbol: str = Query(default="BTC"),
    venue: str = Query(description="Venue: deribit | binance | okx | bybit | cme"),
    contract: str = Query(description="Contract label e.g. BTC-28MAR25"),
    days: int = Query(default=89, ge=1, le=365),
) -> BasisHistoryOut:
    """
    Historical basis time-series for a single venue + contract.

    Deribit: up to 89 days of hourly data from Amberdata (reference repo).
    Binance / OKX / Bybit: daily OHLCV klines (index price is current snapshot proxy).
    CME: Amberdata OHLCV (skipped gracefully if subscription unavailable).
    """
    from app.services.basis_service import _parse_contract_expiry

    series = await get_basis_history(symbol.upper(), venue, contract, days)
    expiry_dt = _parse_contract_expiry(contract)
    return BasisHistoryOut(
        symbol=symbol.upper(),
        venue=venue.lower(),
        contract=contract,
        expiry=expiry_dt.isoformat() if expiry_dt else None,
        series=[
            BasisHistoryPoint(
                timestamp=p.timestamp,
                basis_usd=p.basis_usd,
                basis_pct_ann=p.basis_pct_ann,
                futures_price=p.futures_price,
                index_price=p.index_price,
                days_to_expiry=p.days_to_expiry,
            )
            for p in series
        ],
    )
