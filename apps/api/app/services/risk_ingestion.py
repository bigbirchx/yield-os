"""
Protocol risk parameter ingestion service.

Fetches directly from Aave v3, Morpho Blue, and Kamino; normalizes into
ProtocolRiskParamsSnapshot rows with raw payloads preserved.

Each connector is called independently so a single source failure does not
abort the others.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.aave_client import AaveClient, AaveReserve
from app.connectors.kamino_client import KaminoClient, KaminoReserve
from app.connectors.morpho_client import MorphoClient, MorphoMarket
from app.core.config import settings
from app.models.risk import ProtocolRiskParamsSnapshot

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Normalization helpers — one per source, grounded in their actual fields
# ---------------------------------------------------------------------------

def _from_aave(reserve: AaveReserve, now: datetime) -> ProtocolRiskParamsSnapshot:
    return ProtocolRiskParamsSnapshot(
        protocol="aave-v3",
        chain="Ethereum",
        asset=reserve.symbol.upper(),
        market_address=reserve.underlying_asset,
        max_ltv=reserve.max_ltv,
        liquidation_threshold=reserve.liq_threshold,
        liquidation_penalty=reserve.liq_penalty,
        borrow_cap_native=reserve.borrow_cap_native,
        supply_cap_native=reserve.supply_cap_native,
        collateral_eligible=reserve.usage_as_collateral_enabled,
        borrowing_enabled=reserve.borrowing_enabled,
        is_active=reserve.is_active and not reserve.is_frozen,
        available_capacity_native=reserve.available_capacity_native,
        raw_payload=reserve.model_dump(by_alias=True),
        snapshot_at=now,
    )


def _from_morpho(market: MorphoMarket, now: datetime) -> ProtocolRiskParamsSnapshot:
    return ProtocolRiskParamsSnapshot(
        protocol="morpho-blue",
        chain="Ethereum",
        asset=market.collateral_token.symbol.upper(),
        debt_asset=market.loan_token.symbol.upper(),
        market_address=market.unique_key,
        max_ltv=market.max_ltv,
        liquidation_threshold=market.liquidation_threshold,
        liquidation_penalty=None,  # not provided separately by Morpho Blue API
        borrow_cap_native=None,    # no hard cap in Morpho Blue
        supply_cap_native=None,
        collateral_eligible=True,  # collateral token is eligible by definition
        borrowing_enabled=True,
        is_active=True,
        available_capacity_native=market.available_capacity_usd,  # USD from API
        raw_payload=market.model_dump(by_alias=True),
        snapshot_at=now,
    )


def _from_kamino(
    reserve: KaminoReserve, market_address: str, now: datetime
) -> ProtocolRiskParamsSnapshot | None:
    if not reserve.liquidity_token:
        return None
    return ProtocolRiskParamsSnapshot(
        protocol="kamino",
        chain="Solana",
        asset=reserve.symbol,
        market_address=market_address,
        max_ltv=reserve.max_ltv_float,
        liquidation_threshold=None,   # not in the metrics endpoint
        liquidation_penalty=None,
        borrow_cap_native=None,       # totalBorrow in native units is a string
        supply_cap_native=None,
        collateral_eligible=True,
        borrowing_enabled=True,
        is_active=True,
        available_capacity_native=reserve.available_capacity_usd,
        raw_payload=reserve.model_dump(by_alias=True),
        snapshot_at=now,
    )


# ---------------------------------------------------------------------------
# Per-protocol ingestion
# ---------------------------------------------------------------------------

async def ingest_aave(db: AsyncSession) -> int:
    if not settings.aave_subgraph_key:
        log.warning("aave_ingestion_skipped", reason="AAVE_SUBGRAPH_KEY not set")
        return 0
    url = settings.aave_subgraph_url.format(key=settings.aave_subgraph_key)
    async with AaveClient(subgraph_url=url) as client:
        reserves = await client.fetch_reserves()
    now = datetime.now(UTC)
    rows = [_from_aave(r, now) for r in reserves]
    db.add_all(rows)
    await db.commit()
    log.info("aave_ingestion_done", rows=len(rows))
    return len(rows)


async def ingest_morpho(db: AsyncSession) -> int:
    async with MorphoClient(api_url=settings.morpho_api_url) as client:
        markets = await client.fetch_markets()
    now = datetime.now(UTC)
    rows = [_from_morpho(m, now) for m in markets]
    db.add_all(rows)
    await db.commit()
    log.info("morpho_ingestion_done", rows=len(rows))
    return len(rows)


async def ingest_kamino(db: AsyncSession) -> int:
    async with KaminoClient(base_url=settings.kamino_api_url) as client:
        markets = await client.fetch_markets()
        rows = []
        now = datetime.now(UTC)
        for mkt in markets[:5]:  # MVP: limit to first 5 markets to avoid rate-limiting
            try:
                reserves = await client.fetch_reserves(mkt.lending_market)
                for r in reserves:
                    row = _from_kamino(r, mkt.lending_market, now)
                    if row:
                        rows.append(row)
            except Exception as exc:
                log.warning(
                    "kamino_market_error",
                    market=mkt.lending_market,
                    error=str(exc),
                )
    db.add_all(rows)
    await db.commit()
    log.info("kamino_ingestion_done", rows=len(rows))
    return len(rows)


async def ingest_all(db: AsyncSession) -> dict[str, int]:
    """Run all three protocol connectors with per-source error isolation."""
    results: dict[str, int] = {}
    for label, fn in [
        ("aave", ingest_aave),
        ("morpho", ingest_morpho),
        ("kamino", ingest_kamino),
    ]:
        try:
            results[label] = await fn(db)
        except Exception as exc:
            log.error("risk_ingestion_error", protocol=label, error=str(exc))
            results[label] = 0
    return results
