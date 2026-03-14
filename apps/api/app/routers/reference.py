"""
Reference endpoints — CoinGecko market reference and asset metadata layer.

All endpoints serve MARKET CONTEXT only.  Protocol-native lending rates,
LTVs, and derivatives routing remain in their dedicated routers.

Endpoints
---------
  GET /api/reference/assets              — latest market snapshot for all tracked assets
  GET /api/reference/assets/{symbol}     — canonical metadata + current metrics for one asset
  GET /api/reference/history/{symbol}    — price / market-cap / 24h-volume time series
  GET /api/reference/global              — global crypto market context (live passthrough)
  GET /api/reference/usage               — CoinGecko API key usage (Pro, live passthrough)
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.coingecko_client import get_client
from app.core.database import get_db
from app.repositories.reference import (
    get_asset_map,
    get_history,
    get_latest_api_usage,
    get_latest_snapshots,
    get_snapshot_by_symbol,
)
from app.services.coingecko_ingestion import TRACKED_ASSETS

router = APIRouter(prefix="/api/reference", tags=["reference"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class MarketSnapshotOut(BaseModel):
    symbol: str
    coingecko_id: str
    current_price_usd: float | None
    market_cap_usd: float | None
    fully_diluted_valuation_usd: float | None
    volume_24h_usd: float | None
    circulating_supply: float | None
    total_supply: float | None
    max_supply: float | None
    price_change_24h_pct: float | None
    snapshot_at: datetime
    source_name: str

    model_config = {"from_attributes": True}


class AssetDetailOut(BaseModel):
    symbol: str
    canonical_symbol: str
    coingecko_id: str | None
    name: str | None
    asset_type: str | None
    chain: str | None
    contract_address: str | None
    market: MarketSnapshotOut | None
    source_name: str


class HistoryPoint(BaseModel):
    snapshot_at: datetime
    price_usd: float | None
    market_cap_usd: float | None
    volume_24h_usd: float | None

    model_config = {"from_attributes": True}


class AssetHistoryOut(BaseModel):
    symbol: str
    coingecko_id: str
    series: list[HistoryPoint]


class GlobalMarketOut(BaseModel):
    total_market_cap_usd: float | None
    total_volume_24h_usd: float | None
    btc_dominance_pct: float | None
    eth_dominance_pct: float | None
    market_cap_change_24h_pct: float | None
    active_cryptocurrencies: int | None
    source_name: str
    fetched_at: str


class ApiUsageOut(BaseModel):
    provider: str
    rate_limit: int | None
    remaining_credits: int | None
    monthly_total_credits: int | None
    snapshot_at: datetime | None
    source_name: str

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/assets", response_model=list[MarketSnapshotOut])
async def list_assets(
    symbols: list[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[MarketSnapshotOut]:
    """
    Latest market snapshot for all tracked assets (or a filtered subset).
    Returns cached DB data — always fresh if ingestion is running.
    """
    snaps = await get_latest_snapshots(db, symbols)
    return [MarketSnapshotOut.model_validate(s) for s in snaps]


@router.get("/assets/{symbol}", response_model=AssetDetailOut)
async def asset_detail(
    symbol: str,
    db: AsyncSession = Depends(get_db),
) -> AssetDetailOut:
    """
    Canonical asset metadata + current market metrics for one symbol.
    Returns asset_type, coingecko_id, name, and the latest market snapshot.
    """
    sym = symbol.upper()
    asset_map = await get_asset_map(db, sym)
    snap = await get_snapshot_by_symbol(db, sym)

    market_out = MarketSnapshotOut.model_validate(snap) if snap else None

    if asset_map:
        return AssetDetailOut(
            symbol=asset_map.symbol,
            canonical_symbol=asset_map.canonical_symbol,
            coingecko_id=asset_map.coingecko_id,
            name=asset_map.name,
            asset_type=asset_map.asset_type,
            chain=asset_map.chain,
            contract_address=asset_map.contract_address,
            market=market_out,
            source_name=asset_map.source_name,
        )

    # Fall back to what we know statically from the tracked assets dict
    cg_id = TRACKED_ASSETS.get(sym)
    return AssetDetailOut(
        symbol=sym,
        canonical_symbol=sym,
        coingecko_id=cg_id,
        name=None,
        asset_type=None,
        chain=None,
        contract_address=None,
        market=market_out,
        source_name="coingecko",
    )


@router.get("/history/{symbol}", response_model=AssetHistoryOut)
async def asset_history(
    symbol: str,
    days: int = Query(default=90, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
) -> AssetHistoryOut:
    """
    Historical price / market cap / 24h volume time series for one symbol.
    Sourced from the market_reference_history table (populated by backfill job).
    """
    sym = symbol.upper()
    cg_id = TRACKED_ASSETS.get(sym, sym.lower())

    rows = await get_history(db, cg_id, days)
    return AssetHistoryOut(
        symbol=sym,
        coingecko_id=cg_id,
        series=[HistoryPoint.model_validate(r) for r in rows],
    )


@router.get("/global", response_model=GlobalMarketOut)
async def global_market() -> GlobalMarketOut:
    """
    Global crypto market context — live passthrough to CoinGecko /global.
    Returns total market cap, 24h volume, BTC/ETH dominance, etc.
    """
    from datetime import UTC

    client = get_client()
    data = await client.global_data()

    mc = data.get("total_market_cap", {})
    vol = data.get("total_volume", {})

    return GlobalMarketOut(
        total_market_cap_usd=mc.get("usd"),
        total_volume_24h_usd=vol.get("usd"),
        btc_dominance_pct=data.get("market_cap_percentage", {}).get("btc"),
        eth_dominance_pct=data.get("market_cap_percentage", {}).get("eth"),
        market_cap_change_24h_pct=data.get("market_cap_change_percentage_24h_usd"),
        active_cryptocurrencies=data.get("active_cryptocurrencies"),
        source_name="coingecko",
        fetched_at=datetime.now(UTC).isoformat(),
    )


@router.get("/usage", response_model=ApiUsageOut)
async def api_usage(db: AsyncSession = Depends(get_db)) -> ApiUsageOut:
    """
    CoinGecko API usage snapshot (Pro endpoint).
    Returns the latest stored usage record.  No-ops gracefully if no API key.
    """
    row = await get_latest_api_usage(db)
    if row:
        return ApiUsageOut(
            provider=row.provider,
            rate_limit=row.rate_limit,
            remaining_credits=row.remaining_credits,
            monthly_total_credits=row.monthly_total_credits,
            snapshot_at=row.snapshot_at,
            source_name="coingecko",
        )
    return ApiUsageOut(
        provider="coingecko",
        rate_limit=None,
        remaining_credits=None,
        monthly_total_credits=None,
        snapshot_at=None,
        source_name="coingecko",
    )
