"""
DeFiLlama ingestion service.

Responsibilities:
  1. ingest_lending()  — fetch /pools, filter for lending protocols, persist to
                         lending_market_snapshots
  2. ingest_staking()  — fetch /pools, filter for staking/LSD protocols, persist
                         to staking_snapshots
  3. backfill_pool()   — fetch /chart/{pool_id} for a single pool and write
                         historical lending_market_snapshot rows
  4. ingest_all()      — convenience wrapper for 1 + 2

Source traceability: raw DeFiLlama payloads are stored verbatim in raw_payload.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.defillama_client import DeFiLlamaClient, DeFiLlamaPool
from app.core.config import settings
from app.models.snapshot import LendingMarketSnapshot
from app.models.staking import StakingSnapshot

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Filter configuration
# ---------------------------------------------------------------------------

LENDING_PROTOCOLS: frozenset[str] = frozenset(
    {
        "aave-v3",
        "aave-v2",
        "compound-v3",
        "compound-v2",
        "morpho-blue",
        "morpho-aave",
        "morpho-aave-v3",
        "spark",
        "euler-v2",
        "euler",
        "kamino",
        "solend",
        "marginfi",
        "drift",
        "clearpool",
    }
)

STAKING_PROTOCOLS: frozenset[str] = frozenset(
    {
        "lido",
        "rocket-pool",
        "coinbase-wrapped-staked-eth",
        "frax-ether",
        "stakewise-v3",
        "stakewise",
        "jito",
        "marinade",
        "stader",
        "kelp-dao",
        "ether.fi",
        "binance-staked-eth",
        "mantle-staked-eth",
    }
)

# DeFiLlama pool symbols we care about for each domain
TRACKED_LENDING_SYMBOLS: frozenset[str] = frozenset(
    {
        "BTC", "WBTC", "CBBTC", "BTCB",
        "ETH", "WETH",
        "SOL",
        "USDC", "USDT", "DAI", "FRAX", "LUSD", "GHO",
    }
)

STAKING_SYMBOLS: frozenset[str] = frozenset(
    {
        "STETH", "WSTETH", "CBETH", "RETH", "FRXETH", "SFRXETH", "SWETH",
        "METH", "EZETH", "WEETH",
        "MSOL", "JITOSOL", "BSOL",
        "ETH", "SOL",
    }
)

# Map staking token -> underlying base asset
STAKING_UNDERLYING: dict[str, str] = {
    "STETH": "ETH", "WSTETH": "ETH", "CBETH": "ETH", "RETH": "ETH",
    "FRXETH": "ETH", "SFRXETH": "ETH", "SWETH": "ETH", "METH": "ETH",
    "EZETH": "ETH", "WEETH": "ETH", "ETH": "ETH",
    "MSOL": "SOL", "JITOSOL": "SOL", "BSOL": "SOL", "SOL": "SOL",
}

# Canonical aliases for query-time lookup (what users type -> DB symbols)
SYMBOL_ALIASES: dict[str, list[str]] = {
    "BTC": ["BTC", "WBTC", "CBBTC", "BTCB"],
    "ETH": ["ETH", "WETH"],
    "SOL": ["SOL"],
    "USDC": ["USDC"],
    "USDT": ["USDT"],
    "DAI": ["DAI"],
    "FRAX": ["FRAX"],
}


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


def _lending_row(pool: DeFiLlamaPool, now: datetime) -> LendingMarketSnapshot:
    return LendingMarketSnapshot(
        symbol=pool.symbol.upper(),
        protocol=pool.project,
        market=pool.pool,
        chain=pool.chain,
        pool_id=pool.pool,
        supply_apy=pool.apy_base,
        borrow_apy=pool.apy_base_borrow,
        reward_supply_apy=pool.apy_reward,
        reward_borrow_apy=pool.apy_reward_borrow,
        utilization=pool.utilization,
        tvl_usd=pool.tvl_usd,
        available_liquidity_usd=pool.available_liquidity_usd,
        raw_payload=pool.model_dump(by_alias=False),
        snapshot_at=now,
    )


def _staking_row(pool: DeFiLlamaPool, now: datetime) -> StakingSnapshot:
    sym = pool.symbol.upper()
    return StakingSnapshot(
        symbol=sym,
        underlying_symbol=STAKING_UNDERLYING.get(sym, sym),
        protocol=pool.project,
        chain=pool.chain,
        pool_id=pool.pool,
        staking_apy=pool.apy,
        base_apy=pool.apy_base,
        reward_apy=pool.apy_reward,
        tvl_usd=pool.tvl_usd,
        raw_payload=pool.model_dump(by_alias=False),
        snapshot_at=now,
    )


# ---------------------------------------------------------------------------
# Ingestion functions
# ---------------------------------------------------------------------------


async def ingest_lending(db: AsyncSession) -> int:
    """
    Fetch all DeFiLlama pools, filter for tracked lending protocols and symbols,
    and persist to lending_market_snapshots. Returns row count.
    """
    async with DeFiLlamaClient(api_key=settings.defillama_api_key) as client:
        all_pools = await client.fetch_pools()

    now = datetime.now(UTC)
    rows = [
        _lending_row(p, now)
        for p in all_pools
        if p.project in LENDING_PROTOCOLS
        and p.symbol.upper() in TRACKED_LENDING_SYMBOLS
    ]
    db.add_all(rows)
    await db.commit()
    log.info("defillama_lending_ingested", rows=len(rows))
    return len(rows)


async def ingest_staking(db: AsyncSession) -> int:
    """
    Fetch all DeFiLlama pools, filter for staking/LSD protocols and symbols,
    and persist to staking_snapshots. Returns row count.
    """
    async with DeFiLlamaClient(api_key=settings.defillama_api_key) as client:
        all_pools = await client.fetch_pools()

    now = datetime.now(UTC)
    rows = [
        _staking_row(p, now)
        for p in all_pools
        if p.project in STAKING_PROTOCOLS
        and p.symbol.upper() in STAKING_SYMBOLS
    ]
    db.add_all(rows)
    await db.commit()
    log.info("defillama_staking_ingested", rows=len(rows))
    return len(rows)


async def backfill_pool(
    db: AsyncSession,
    pool_id: str,
    symbol: str,
    protocol: str,
    chain: str,
) -> int:
    """
    Fetch the full daily history for one DeFiLlama pool and insert it as
    lending_market_snapshot rows (one per day). Existing rows are not deleted;
    re-running is safe because snapshot_at reflects the original data timestamp.
    Returns the number of rows written.
    """
    async with DeFiLlamaClient(api_key=settings.defillama_api_key) as client:
        history = await client.fetch_pool_chart(pool_id)

    rows = []
    for point in history:
        try:
            snapshot_at = datetime.fromisoformat(point.timestamp.replace("Z", "+00:00"))
        except ValueError:
            log.warning("defillama_backfill_bad_ts", ts=point.timestamp)
            continue

        rows.append(
            LendingMarketSnapshot(
                symbol=symbol.upper(),
                protocol=protocol,
                market=pool_id,
                chain=chain,
                pool_id=pool_id,
                supply_apy=point.apy_base,
                borrow_apy=point.apy_base_borrow,
                reward_supply_apy=point.apy_reward,
                tvl_usd=point.tvl_usd,
                raw_payload=point.model_dump(by_alias=False),
                snapshot_at=snapshot_at,
            )
        )

    db.add_all(rows)
    await db.commit()
    log.info("defillama_backfill_done", pool_id=pool_id, rows=len(rows))
    return len(rows)


async def ingest_all(db: AsyncSession) -> dict[str, int]:
    """Run lending and staking ingestion with per-domain error isolation."""
    results: dict[str, int] = {}
    for label, fn in [("lending", ingest_lending), ("staking", ingest_staking)]:
        try:
            results[label] = await fn(db)
        except Exception as exc:
            log.error("defillama_ingest_error", domain=label, error=str(exc))
            results[label] = 0
    return results
