"""
CoinGecko ingestion service.

Jobs:
  ingest_market_snapshots(db)  — /coins/markets for tracked assets → market_reference_snapshots
  ingest_asset_map(db)         — /coins/list + filter  → asset_reference_map (upsert)
  ingest_api_usage(db)         — /key                  → api_usage_snapshots (Pro only)
  backfill_history(db, ...)    — /coins/{id}/market_chart → market_reference_history

Tracked assets
--------------
  BTC family  : BTC, WBTC, CBBTC
  ETH family  : ETH, WETH, stETH, wstETH, rETH
  SOL family  : SOL
  Stablecoins : USDC, USDT, DAI, PYUSD
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.coingecko_client import get_client
from app.models.reference import (
    ApiUsageSnapshot,
    AssetReferenceMap,
    MarketReferenceHistory,
    MarketReferenceSnapshot,
)

log = structlog.get_logger(__name__)

SOURCE = "coingecko"

# symbol → coingecko_id mapping for all tracked Yield OS assets
TRACKED_ASSETS: dict[str, str] = {
    # BTC family
    "BTC":   "bitcoin",
    "WBTC":  "wrapped-bitcoin",
    "CBBTC": "coinbase-wrapped-btc",
    # ETH family
    "ETH": "ethereum",
    "WETH": "weth",
    "stETH": "staked-ether",
    "wstETH": "wrapped-steth",
    "rETH": "rocket-pool-eth",
    # SOL family
    "SOL": "solana",
    # Major USD stablecoins
    "USDC": "usd-coin",
    "USDT": "tether",
    "DAI": "dai",
    "PYUSD": "paypal-usd",
}

# Reverse map for lookup by coingecko_id
_ID_TO_SYMBOL: dict[str, str] = {v: k for k, v in TRACKED_ASSETS.items()}

# Asset type classification
_ASSET_TYPE: dict[str, str] = {
    "BTC":   "crypto",
    "ETH":   "crypto",
    "SOL":   "crypto",
    "WBTC":  "wrapper",
    "CBBTC": "wrapper",
    "WETH":  "wrapper",
    "stETH": "lst",
    "wstETH": "lst",
    "rETH": "lst",
    "USDC": "stablecoin",
    "USDT": "stablecoin",
    "DAI": "stablecoin",
    "PYUSD": "stablecoin",
}


def _sf(val: Any) -> float | None:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Market snapshots
# ---------------------------------------------------------------------------


async def ingest_market_snapshots(db: AsyncSession) -> int:
    """
    Fetch current market data for all tracked assets and store a new snapshot.
    Returns the number of rows inserted.
    """
    client = get_client()
    ids = list(TRACKED_ASSETS.values())
    rows = await client.coins_markets(ids=ids)
    if not rows:
        log.warning("coingecko_market_snapshot_empty")
        return 0

    now = datetime.now(UTC)
    inserted = 0
    for item in rows:
        cg_id = item.get("id", "")
        symbol = _ID_TO_SYMBOL.get(cg_id, item.get("symbol", "").upper())
        snap = MarketReferenceSnapshot(
            snapshot_at=now,
            coingecko_id=cg_id,
            symbol=symbol,
            current_price_usd=_sf(item.get("current_price")),
            market_cap_usd=_sf(item.get("market_cap")),
            fully_diluted_valuation_usd=_sf(item.get("fully_diluted_valuation")),
            volume_24h_usd=_sf(item.get("total_volume")),
            circulating_supply=_sf(item.get("circulating_supply")),
            total_supply=_sf(item.get("total_supply")),
            max_supply=_sf(item.get("max_supply")),
            price_change_24h_pct=_sf(item.get("price_change_percentage_24h")),
            source_name=SOURCE,
            raw_payload=item,
        )
        db.add(snap)
        inserted += 1

    await db.commit()
    log.info("coingecko_market_snapshot_done", inserted=inserted)
    return inserted


# ---------------------------------------------------------------------------
# Asset reference map
# ---------------------------------------------------------------------------


async def ingest_asset_map(db: AsyncSession) -> int:
    """
    Upsert asset reference map rows from the tracked assets dict.
    Fetches /coins/list to enrich with name data.
    Returns number of rows upserted.
    """
    client = get_client()
    coin_list = await client.coins_list()
    name_map: dict[str, str] = {c["id"]: c["name"] for c in coin_list} if coin_list else {}

    upserted = 0
    for symbol, cg_id in TRACKED_ASSETS.items():
        stmt = select(AssetReferenceMap).where(
            AssetReferenceMap.symbol == symbol,
            AssetReferenceMap.source_name == SOURCE,
        )
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()

        name = name_map.get(cg_id)
        if existing:
            existing.coingecko_id = cg_id
            existing.canonical_symbol = symbol
            if name:
                existing.name = name
            existing.asset_type = _ASSET_TYPE.get(symbol)
        else:
            db.add(
                AssetReferenceMap(
                    symbol=symbol,
                    canonical_symbol=symbol,
                    coingecko_id=cg_id,
                    name=name,
                    asset_type=_ASSET_TYPE.get(symbol),
                    source_name=SOURCE,
                )
            )
        upserted += 1

    await db.commit()
    log.info("coingecko_asset_map_done", upserted=upserted)
    return upserted


# ---------------------------------------------------------------------------
# API usage snapshot
# ---------------------------------------------------------------------------


async def ingest_api_usage(db: AsyncSession) -> bool:
    """
    Fetch /key usage data and store a snapshot.
    Returns True if data was retrieved and stored.
    """
    client = get_client()
    data = await client.api_key_info()
    if not data:
        log.debug("coingecko_api_usage_skipped", reason="no_key_or_empty_response")
        return False

    plan = data.get("plan", {})
    usage = data.get("usage", {})
    monthly = usage.get("monthly", {})

    snap = ApiUsageSnapshot(
        snapshot_at=datetime.now(UTC),
        provider=SOURCE,
        rate_limit=plan.get("rate_limit_request_per_minute"),
        remaining_credits=monthly.get("remaining_credits"),
        monthly_total_credits=monthly.get("total_credits"),
        raw_payload=data,
    )
    db.add(snap)
    await db.commit()
    log.info("coingecko_api_usage_stored")
    return True


# ---------------------------------------------------------------------------
# Historical backfill
# ---------------------------------------------------------------------------


async def backfill_history(
    db: AsyncSession,
    coingecko_id: str,
    days: int = 365,
) -> int:
    """
    Fetch historical price/mcap/volume from /coins/{id}/market_chart and
    store daily rows into market_reference_history.

    Skips days already present in the DB (based on coingecko_id + snapshot_at).
    Returns the number of rows inserted.
    """
    client = get_client()
    data = await client.market_chart(coingecko_id, days=days)
    if not data:
        log.warning("coingecko_backfill_empty", coingecko_id=coingecko_id)
        return 0

    prices = data.get("prices", [])
    market_caps = data.get("market_caps", [])
    volumes = data.get("total_volumes", [])

    # Index by timestamp (ms) for O(1) lookup
    mc_map = {int(row[0]): row[1] for row in market_caps}
    vol_map = {int(row[0]): row[1] for row in volumes}

    # Fetch existing timestamps to avoid duplicates
    stmt = select(MarketReferenceHistory.snapshot_at).where(
        MarketReferenceHistory.coingecko_id == coingecko_id
    )
    result = await db.execute(stmt)
    existing_ts = {row[0].replace(tzinfo=UTC) for row in result.fetchall()}

    inserted = 0
    for ts_ms, price in prices:
        ts_ms = int(ts_ms)
        ts = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
        if ts in existing_ts:
            continue
        db.add(
            MarketReferenceHistory(
                snapshot_at=ts,
                coingecko_id=coingecko_id,
                price_usd=_sf(price),
                market_cap_usd=_sf(mc_map.get(ts_ms)),
                volume_24h_usd=_sf(vol_map.get(ts_ms)),
                source_name=SOURCE,
                raw_payload={"ts_ms": ts_ms, "price": price},
            )
        )
        inserted += 1

    if inserted:
        await db.commit()
    log.info("coingecko_backfill_done", coingecko_id=coingecko_id, inserted=inserted)
    return inserted


async def backfill_all(db: AsyncSession, days: int = 365) -> dict[str, int]:
    """Backfill history for all tracked assets.  Returns {coingecko_id: rows_inserted}."""
    results: dict[str, int] = {}
    for _symbol, cg_id in TRACKED_ASSETS.items():
        results[cg_id] = await backfill_history(db, cg_id, days=days)
    return results
