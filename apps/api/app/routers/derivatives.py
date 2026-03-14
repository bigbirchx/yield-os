from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.repositories.derivatives import get_latest_per_symbol, get_latest_per_venue
from app.services.velo_ingestion import TRACKED_COINS

router = APIRouter(prefix="/api/derivatives", tags=["derivatives"])


class DerivativesSnapshotOut(BaseModel):
    symbol: str
    venue: str
    funding_rate: float | None
    open_interest_usd: float | None
    basis_annualized: float | None
    mark_price: float | None
    index_price: float | None
    spot_volume_usd: float | None
    perp_volume_usd: float | None
    snapshot_at: datetime
    ingested_at: datetime

    model_config = {"from_attributes": True}


class DerivativesOverviewOut(BaseModel):
    symbol: str
    venues: list[DerivativesSnapshotOut]


@router.get("/overview", response_model=list[DerivativesOverviewOut])
async def derivatives_overview(
    symbols: list[str] = Query(default=TRACKED_COINS),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns the latest derivatives snapshot per (symbol, venue) for the
    requested symbols (default: BTC, ETH, SOL).

    Source: Velo
    """
    rows = await get_latest_per_symbol(db, symbols)

    # Group by symbol
    grouped: dict[str, list] = {s.upper(): [] for s in symbols}
    for row in rows:
        grouped.setdefault(row.symbol, []).append(
            DerivativesSnapshotOut.model_validate(row)
        )

    return [
        DerivativesOverviewOut(symbol=sym, venues=venues)
        for sym, venues in grouped.items()
    ]


@router.get("/{symbol}", response_model=list[DerivativesSnapshotOut])
async def derivatives_by_symbol(
    symbol: str,
    db: AsyncSession = Depends(get_db),
):
    """Returns the latest snapshot per venue for a single symbol."""
    rows = await get_latest_per_venue(db, symbol)
    return [DerivativesSnapshotOut.model_validate(r) for r in rows]
