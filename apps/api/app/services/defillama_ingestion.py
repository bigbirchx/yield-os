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


# ===========================================================================
# Extended ingestion: protocols, chains, stablecoins, market context
# ===========================================================================

import asyncio
from app.connectors.defillama_client import (
    DeFiLlamaMainClient,
    DeFiLlamaStablecoinsClient,
)
from app.models.defillama import (
    DLYieldPoolSnapshot,
    DLYieldPoolHistory,
    DLProtocolSnapshot,
    DLChainTvlHistory,
    DLStablecoinSnapshot,
    DLStablecoinHistory,
    DLMarketContextSnapshot,
)

# ---------------------------------------------------------------------------
# Tracked coverage
# ---------------------------------------------------------------------------

TRACKED_PROTOCOLS: frozenset[str] = frozenset({
    "aave-v3", "aave-v2", "compound-v3", "compound-v2",
    "morpho-blue", "morpho-aave-v3", "spark", "euler-v2",
    "lido", "rocket-pool", "coinbase-wrapped-staked-eth",
    "maker", "curve-dex", "uniswap", "uniswap-v3",
    "hyperliquid", "dydx", "gmx", "gmx-v2",
    "jupiter", "kamino", "marginfi",
})

TRACKED_CHAINS: list[str] = [
    "Ethereum", "Arbitrum", "Base", "Optimism", "Polygon",
    "Solana", "Avalanche", "BNB", "Blast",
]

TRACKED_STABLECOIN_SYMBOLS: frozenset[str] = frozenset({
    "USDC", "USDT", "DAI", "FRAX", "USDS", "GHO",
    "PYUSD", "FDUSD", "TUSD", "USDP", "GUSD",
})

_DL_SOURCE = "defillama_free"


# ---------------------------------------------------------------------------
# Yield pool snapshot (new dedicated table)
# ---------------------------------------------------------------------------

TRACKED_YIELD_SYMBOLS: frozenset[str] = frozenset({
    "BTC", "WBTC", "CBBTC",
    "ETH", "WETH", "STETH", "WSTETH",
    "SOL", "MSOL", "JITOSOL",
    "USDC", "USDT", "DAI", "FRAX", "GHO",
})

TRACKED_YIELD_PROTOCOLS: frozenset[str] = LENDING_PROTOCOLS | STAKING_PROTOCOLS | frozenset({
    "curve-dex", "uniswap-v3", "balancer-v2", "pendle",
    "kelp-dao", "ether.fi", "renzo",
})


async def ingest_yield_pool_snapshots(db: AsyncSession) -> int:
    """
    Fetch /pools and persist snapshots for tracked symbols/protocols to
    defillama_yield_pool_snapshot. Extends the existing lending/staking ingestion.
    """
    async with DeFiLlamaClient(api_key=settings.defillama_api_key) as client:
        all_pools = await client.fetch_pools()

    now = datetime.now(UTC)
    rows = []
    for p in all_pools:
        sym = p.symbol.upper()
        if (
            p.project in TRACKED_YIELD_PROTOCOLS
            or sym in TRACKED_YIELD_SYMBOLS
        ) and p.tvl_usd and p.tvl_usd > 100_000:
            rows.append(DLYieldPoolSnapshot(
                snapshot_at=now,
                pool_id=p.pool,
                project=p.project,
                chain=p.chain,
                symbol=sym,
                tvl_usd=p.tvl_usd,
                apy=p.apy,
                apy_base=p.apy_base,
                apy_reward=p.apy_reward,
                stablecoin=p.stablecoin,
                il_risk=p.il_risk,
                exposure=p.exposure,
                predictions=p.predictions,
                source_name=_DL_SOURCE,
                raw=p.model_dump(by_alias=False),
            ))
    db.add_all(rows)
    await db.commit()
    log.info("defillama_yield_snapshots_ingested", rows=len(rows))
    return len(rows)


async def backfill_yield_pool_history(
    db: AsyncSession,
    pool_id: str,
) -> int:
    """
    Fetch /chart/{pool_id} and write daily history rows to
    defillama_yield_pool_history. Safe to re-run (appends new rows only).
    """
    async with DeFiLlamaClient(api_key=settings.defillama_api_key) as client:
        history = await client.fetch_pool_chart(pool_id)

    rows = []
    for point in history:
        try:
            ts = datetime.fromisoformat(point.timestamp.replace("Z", "+00:00"))
        except ValueError:
            continue
        rows.append(DLYieldPoolHistory(
            ts=ts,
            pool_id=pool_id,
            apy=point.apy,
            tvl_usd=point.tvl_usd,
            apy_base=point.apy_base,
            apy_reward=point.apy_reward,
            source_name=_DL_SOURCE,
            raw=point.model_dump(by_alias=False),
        ))
    db.add_all(rows)
    await db.commit()
    log.info("defillama_yield_history_backfilled", pool_id=pool_id, rows=len(rows))
    return len(rows)


# ---------------------------------------------------------------------------
# Protocol snapshots
# ---------------------------------------------------------------------------

async def ingest_protocols(db: AsyncSession) -> int:
    """
    Fetch /protocols, filter for tracked names, persist to
    defillama_protocol_snapshot.
    """
    async with DeFiLlamaMainClient() as client:
        all_protos = await client.fetch_protocols()

    now = datetime.now(UTC)
    rows = []
    for p in all_protos:
        slug = p.slug or p.name.lower().replace(" ", "-")
        if slug not in TRACKED_PROTOCOLS:
            continue
        rows.append(DLProtocolSnapshot(
            ts=now,
            protocol_slug=slug,
            protocol_name=p.name,
            category=p.category,
            chain=p.chain,
            tvl_usd=p.tvl,
            change_1d=p.change_1d,
            change_7d=p.change_7d,
            change_1m=p.change_1m,
            source_name=_DL_SOURCE,
            raw=p.model_dump(by_alias=False),
        ))
    db.add_all(rows)
    await db.commit()
    log.info("defillama_protocols_ingested", rows=len(rows))
    return len(rows)


# ---------------------------------------------------------------------------
# Chain TVL history
# ---------------------------------------------------------------------------

async def ingest_chain_tvl(db: AsyncSession) -> int:
    """
    Fetch /v2/historicalChainTvl/{chain} for each tracked chain and persist
    to defillama_chain_tvl_history. Runs all chains concurrently.
    """
    async with DeFiLlamaMainClient() as client:
        results = await asyncio.gather(
            *[client.fetch_chain_tvl_history(c) for c in TRACKED_CHAINS],
            return_exceptions=True,
        )

    rows = []
    from datetime import timezone as _tz
    for chain, result in zip(TRACKED_CHAINS, results):
        if isinstance(result, Exception):
            log.warning("defillama_chain_tvl_error", chain=chain, error=str(result))
            continue
        for pt in result:
            ts = datetime.fromtimestamp(pt.date, tz=_tz.utc)
            rows.append(DLChainTvlHistory(
                ts=ts,
                chain=chain,
                tvl_usd=pt.tvl,
                source_name=_DL_SOURCE,
                raw={"date": pt.date, "tvl": pt.tvl},
            ))
    db.add_all(rows)
    await db.commit()
    log.info("defillama_chain_tvl_ingested", rows=len(rows))
    return len(rows)


# ---------------------------------------------------------------------------
# Stablecoins
# ---------------------------------------------------------------------------

async def ingest_stablecoins(db: AsyncSession) -> int:
    """
    Fetch /stablecoins and /stablecoincharts/all, persist to
    defillama_stablecoin_snapshot and defillama_stablecoin_history.
    """
    async with DeFiLlamaStablecoinsClient() as client:
        stables, charts = await asyncio.gather(
            client.fetch_stablecoins(),
            client.fetch_stablecoin_charts(),
        )

    now = datetime.now(UTC)
    snap_rows = []
    for s in stables:
        if s.symbol.upper() not in TRACKED_STABLECOIN_SYMBOLS:
            continue
        snap_rows.append(DLStablecoinSnapshot(
            ts=now,
            stablecoin_id=str(s.id),
            symbol=s.symbol.upper(),
            circulating_usd=s.circulating_usd,
            chains=s.chain_circulating,
            peg_type=s.peg_type,
            peg_mechanism=s.peg_mechanism,
            source_name=_DL_SOURCE,
            raw=s.model_dump(by_alias=False),
        ))
    db.add_all(snap_rows)

    from datetime import timezone as _tz
    hist_rows = []
    for pt in charts:
        ts = datetime.fromtimestamp(pt.date, tz=_tz.utc)
        hist_rows.append(DLStablecoinHistory(
            ts=ts,
            chain=None,
            circulating_usd=pt.circulating_usd,
            source_name=_DL_SOURCE,
            raw={"date": pt.date, "totalCirculatingUSD": pt.total_circulating_usd},
        ))
    db.add_all(hist_rows)
    await db.commit()
    total = len(snap_rows) + len(hist_rows)
    log.info("defillama_stablecoins_ingested", snapshots=len(snap_rows), history=len(hist_rows))
    return total


# ---------------------------------------------------------------------------
# Market context (DEX volume, OI, Fees)
# ---------------------------------------------------------------------------

_MARKET_CONTEXT_PROTOCOLS: frozenset[str] = frozenset({
    "uniswap", "uniswap-v3", "curve-dex", "hyperliquid",
    "dydx", "gmx", "gmx-v2", "jupiter", "orca",
    "binance", "okx", "bybit",
})


async def ingest_market_context(db: AsyncSession) -> int:
    """
    Fetch /overview/dexs, /overview/open-interest, /overview/fees and persist
    aggregate + per-protocol rows to defillama_market_context_snapshot.
    """
    async with DeFiLlamaMainClient() as client:
        dexs_resp, oi_resp, fees_resp = await asyncio.gather(
            client.fetch_overview_dexs(),
            client.fetch_overview_open_interest(),
            client.fetch_overview_fees(),
            return_exceptions=True,
        )

    now = datetime.now(UTC)
    rows = []

    def _add_overview(resp, context_type: str) -> None:
        if isinstance(resp, Exception):
            log.warning("defillama_market_context_error", context=context_type, error=str(resp))
            return
        # Aggregate row
        rows.append(DLMarketContextSnapshot(
            ts=now,
            context_type=context_type,
            protocol_or_chain="_aggregate",
            metric_name="total_24h",
            metric_value=resp.total_24h,
            source_name=_DL_SOURCE,
            raw={"total_24h": resp.total_24h, "total_48h_to_24h": resp.total_48h_to_24h},
        ))
        # Per-protocol rows
        for proto in resp.protocols:
            slug = (proto.slug or proto.name).lower().replace(" ", "-")
            rows.append(DLMarketContextSnapshot(
                ts=now,
                context_type=context_type,
                protocol_or_chain=slug,
                metric_name="total_24h",
                metric_value=proto.total_24h,
                source_name=_DL_SOURCE,
                raw=proto.model_dump(by_alias=False),
            ))

    _add_overview(dexs_resp, "dex_volume")
    _add_overview(oi_resp, "open_interest")
    _add_overview(fees_resp, "fees_revenue")

    db.add_all(rows)
    await db.commit()
    log.info("defillama_market_context_ingested", rows=len(rows))
    return len(rows)


# ---------------------------------------------------------------------------
# Updated ingest_all — includes all new jobs
# ---------------------------------------------------------------------------

async def ingest_all_extended(db: AsyncSession) -> dict[str, int]:
    """
    Run all DefiLlama ingestion jobs with per-domain error isolation.
    Replaces the legacy ingest_all for the extended pipeline.
    """
    jobs = {
        "lending": ingest_lending,
        "staking": ingest_staking,
        "yield_snapshots": ingest_yield_pool_snapshots,
        "protocols": ingest_protocols,
        "stablecoins": ingest_stablecoins,
        "market_context": ingest_market_context,
    }
    results: dict[str, int] = {}
    for label, fn in jobs.items():
        try:
            results[label] = await fn(db)
        except Exception as exc:
            log.error("defillama_ingest_error", domain=label, error=str(exc))
            results[label] = 0
    return results
