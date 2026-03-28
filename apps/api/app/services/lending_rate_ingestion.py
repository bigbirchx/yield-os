"""
Protocol-native borrow-rate ingestion service.

Fetches current borrow and supply APY directly from Aave v3, Kamino, and
Morpho Blue and writes normalized rows to lending_market_snapshots.

APY encoding note (matches lending_market_snapshots convention):
  - Values stored as percentages: 5.0 means 5% APY
  - Aave API returns decimal fractions (0.05 = 5%) -> multiply * 100
  - Kamino API returns decimal fractions (0.05 = 5%) -> multiply * 100
  - Morpho Blue API returns percentages directly (5.0 = 5%) -> use as-is
  - Utilization stored as decimal fraction 0-1 for all sources

Source labels: "aave", "kamino", "morpho_blue"
Raw payloads preserved for reconciliation.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.aave_client import AaveClient, AaveReserve, DEFAULT_CHAIN_IDS
from app.connectors.kamino_client import KaminoClient, KaminoReserveMetrics
from app.connectors.morpho_client import MorphoClient, MorphoMarket
from app.core.config import settings
from app.models.snapshot import LendingMarketSnapshot

log = structlog.get_logger(__name__)


def _aave_row(reserve: AaveReserve, now: datetime) -> LendingMarketSnapshot | None:
    if not reserve.is_active or not reserve.borrow_info.borrowing_enabled:
        return None

    # Aave returns decimal fractions; convert to percentages for storage
    borrow_apy_raw = reserve.borrow_info.apy.as_float
    supply_apy_raw = reserve.supply_info.apy.as_float
    borrow_apy = borrow_apy_raw * 100.0 if borrow_apy_raw else None
    supply_apy = supply_apy_raw * 100.0 if supply_apy_raw else None

    # Utilization is already a decimal fraction (0-1)
    utilization_raw = reserve.borrow_info.utilization_rate.as_float
    utilization = utilization_raw if utilization_raw and utilization_raw > 0 else None

    return LendingMarketSnapshot(
        symbol=reserve.symbol,
        protocol="aave",
        market=reserve.market_name,
        chain=reserve.chain_name or "Ethereum",
        pool_id=f"aave:{reserve.chain_id}:{reserve.underlying_token.address}",
        supply_apy=supply_apy,
        borrow_apy=borrow_apy,
        reward_supply_apy=None,
        reward_borrow_apy=None,
        utilization=utilization,
        tvl_usd=reserve.supply_info.supply_cap.usd_float,
        available_liquidity_usd=reserve.available_capacity_usd,
        borrow_cap_usd=reserve.borrow_info.borrow_cap.usd_float,
        supply_cap_usd=reserve.supply_info.supply_cap.usd_float,
        raw_payload=reserve.model_dump(by_alias=True),
        snapshot_at=now,
    )


def _kamino_row(
    reserve: KaminoReserveMetrics, market_address: str, market_name: str | None, now: datetime
) -> LendingMarketSnapshot | None:
    if not reserve.liquidity_token:
        return None

    # Kamino returns decimal fractions; convert to percentages for storage
    borrow_apy = reserve.borrow_apy * 100.0 if reserve.borrow_apy is not None else None
    supply_apy = reserve.supply_apy * 100.0 if reserve.supply_apy is not None else None

    utilization: float | None = None
    if reserve.total_supply_usd and reserve.total_borrow_usd and reserve.total_supply_usd > 0:
        utilization = min(reserve.total_borrow_usd / reserve.total_supply_usd, 1.0)

    return LendingMarketSnapshot(
        symbol=reserve.symbol,
        protocol="kamino",
        market=market_name or market_address,
        chain="Solana",
        pool_id=f"kamino:{market_address}:{reserve.reserve}",
        supply_apy=supply_apy,
        borrow_apy=borrow_apy,
        reward_supply_apy=None,
        reward_borrow_apy=None,
        utilization=utilization,
        tvl_usd=reserve.total_supply_usd,
        available_liquidity_usd=reserve.available_capacity_usd,
        borrow_cap_usd=None,
        supply_cap_usd=None,
        raw_payload=reserve.model_dump(by_alias=True),
        snapshot_at=now,
    )


def _morpho_row(market: MorphoMarket, now: datetime) -> LendingMarketSnapshot | None:
    """One row per Morpho Blue isolated market.

    symbol = loan_token (the borrowed asset); collateral preserved in raw payload.
    Morpho returns APY as decimal fractions (0.05 = 5%) — multiply by 100 for storage.
    The GraphQL query already filters to borrowApy_lte:5 (<=500% APY).
    """
    borrow_apy_raw = market.borrow_apy
    if borrow_apy_raw is None:
        return None
    # Convert decimal fraction to percentage format for storage
    borrow_apy = borrow_apy_raw * 100.0
    supply_apy = market.supply_apy * 100.0 if market.supply_apy is not None else None

    market_label = f"{market.collateral_token.symbol}/{market.loan_token.symbol}"
    return LendingMarketSnapshot(
        symbol=market.loan_token.symbol.upper(),
        protocol="morpho_blue",
        market=market_label,
        chain="Ethereum",
        pool_id=f"morpho:{market.unique_key}",
        supply_apy=supply_apy,
        borrow_apy=borrow_apy,
        reward_supply_apy=None,
        reward_borrow_apy=None,
        utilization=market.utilization,
        tvl_usd=market.total_supply_usd,
        available_liquidity_usd=market.available_capacity_usd,
        borrow_cap_usd=None,
        supply_cap_usd=None,
        raw_payload=market.model_dump(by_alias=True),
        snapshot_at=now,
    )


async def ingest_aave_borrow_rates(db: AsyncSession) -> int:
    chain_ids = [int(c.strip()) for c in settings.aave_chain_ids.split(",") if c.strip()]
    async with AaveClient(api_url=settings.aave_api_url) as client:
        reserves = await client.fetch_reserves(chain_ids=chain_ids)
    now = datetime.now(UTC)
    rows = [r for reserve in reserves if (r := _aave_row(reserve, now))]
    db.add_all(rows)
    await db.commit()
    log.info("aave_borrow_rate_ingestion_done", rows=len(rows), chains=chain_ids)
    return len(rows)


async def ingest_kamino_borrow_rates(db: AsyncSession) -> int:
    async with KaminoClient(base_url=settings.kamino_api_url) as client:
        markets = await client.fetch_markets()
        rows = []
        now = datetime.now(UTC)
        for mkt in markets[:10]:
            try:
                reserves = await client.fetch_reserves(mkt.lending_market)
                for r in reserves:
                    row = _kamino_row(r, mkt.lending_market, mkt.name, now)
                    if row:
                        rows.append(row)
            except Exception as exc:
                log.warning("kamino_borrow_rate_market_error", market=mkt.lending_market, error=str(exc))
    db.add_all(rows)
    await db.commit()
    log.info("kamino_borrow_rate_ingestion_done", rows=len(rows))
    return len(rows)


async def ingest_morpho_borrow_rates(db: AsyncSession) -> int:
    async with MorphoClient(api_url=settings.morpho_api_url) as client:
        markets = await client.fetch_markets()
    now = datetime.now(UTC)
    rows = [r for m in markets if (r := _morpho_row(m, now))]
    db.add_all(rows)
    await db.commit()
    log.info("morpho_borrow_rate_ingestion_done", rows=len(rows))
    return len(rows)


async def ingest_all_borrow_rates(db: AsyncSession) -> dict[str, int]:
    """Run all three protocol-native borrow-rate jobs with per-source isolation."""
    results: dict[str, int] = {}
    for label, fn in [
        ("aave", ingest_aave_borrow_rates),
        ("kamino", ingest_kamino_borrow_rates),
        ("morpho_blue", ingest_morpho_borrow_rates),
    ]:
        try:
            results[label] = await fn(db)
        except Exception as exc:
            log.error("borrow_rate_ingestion_error", protocol=label, error=str(exc))
            results[label] = 0
    return results
