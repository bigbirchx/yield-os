from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.repositories.lending import get_history, get_latest_per_market
from app.services.defillama_ingestion import SYMBOL_ALIASES, TRACKED_LENDING_SYMBOLS

router = APIRouter(tags=["lending"])

_DEFAULT_OVERVIEW_SYMBOLS = ["USDC", "USDT", "ETH", "WBTC", "CBBTC", "SOL"]


# ---------------------------------------------------------------------------
# Output schemas
# ---------------------------------------------------------------------------


class LendingMarketOut(BaseModel):
    symbol: str
    protocol: str
    market: str
    chain: str | None
    pool_id: str | None
    supply_apy: float | None
    borrow_apy: float | None
    reward_supply_apy: float | None
    reward_borrow_apy: float | None
    utilization: float | None
    tvl_usd: float | None
    available_liquidity_usd: float | None
    snapshot_at: datetime
    ingested_at: datetime

    model_config = {"from_attributes": True}


class LendingOverviewSymbol(BaseModel):
    symbol: str
    markets: list[LendingMarketOut]


class LendingHistoryPoint(BaseModel):
    snapshot_at: datetime
    supply_apy: float | None
    borrow_apy: float | None
    reward_supply_apy: float | None
    tvl_usd: float | None
    utilization: float | None

    model_config = {"from_attributes": True}


class LendingHistoryMarket(BaseModel):
    protocol: str
    market: str
    chain: str | None
    data: list[LendingHistoryPoint]


class AssetHistoryOut(BaseModel):
    symbol: str
    lending: list[LendingHistoryMarket]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/api/lending/overview", response_model=list[LendingOverviewSymbol])
async def lending_overview(
    symbols: list[str] = Query(default=_DEFAULT_OVERVIEW_SYMBOLS),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns the latest lending market snapshot per (symbol, protocol, market).

    Expands symbol aliases (e.g. ETH -> ETH, WETH) so protocol-native rows that
    store "WETH" are returned when the caller queries "ETH".

    Sources: protocol-native connectors (Aave, Kamino, Morpho Blue) + DeFiLlama
    """
    symbols_upper = [s.upper() for s in symbols]

    # Build the full lookup set including aliases
    lookup_set: set[str] = set()
    canonical_map: dict[str, str] = {}  # db_symbol -> canonical query symbol
    for sym in symbols_upper:
        aliases = SYMBOL_ALIASES.get(sym, [sym])
        for alias in aliases:
            lookup_set.add(alias)
            canonical_map[alias] = sym

    rows = await get_latest_per_market(db, list(lookup_set))

    grouped: dict[str, list[LendingMarketOut]] = {s: [] for s in symbols_upper}
    for row in rows:
        canonical = canonical_map.get(row.symbol.upper(), row.symbol.upper())
        if canonical in grouped:
            grouped[canonical].append(LendingMarketOut.model_validate(row))

    return [
        LendingOverviewSymbol(symbol=sym, markets=markets)
        for sym, markets in grouped.items()
    ]


@router.get("/api/assets/{symbol}/history", response_model=AssetHistoryOut)
async def asset_lending_history(
    symbol: str,
    days: int = Query(default=30, ge=1, le=365),
    protocol: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns time-series lending market data for the requested symbol.

    Resolves symbol aliases (e.g. BTC -> BTC, WBTC, CBBTC) so that querying
    /api/assets/BTC/history returns data for all BTC-family tokens.

    Source: DeFiLlama yields API (historical charts)
    """
    lookup_symbols = SYMBOL_ALIASES.get(symbol.upper(), [symbol.upper()])
    rows = await get_history(db, lookup_symbols, days=days, protocol=protocol)

    # Group by (protocol, market)
    markets: dict[tuple[str, str], LendingHistoryMarket] = {}
    for row in rows:
        key = (row.protocol, row.market)
        if key not in markets:
            markets[key] = LendingHistoryMarket(
                protocol=row.protocol,
                market=row.market,
                chain=row.chain,
                data=[],
            )
        markets[key].data.append(LendingHistoryPoint.model_validate(row))

    return AssetHistoryOut(symbol=symbol.upper(), lending=list(markets.values()))
