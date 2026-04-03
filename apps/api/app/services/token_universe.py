"""
Token universe and price service.

Maintains a merged view of the static :data:`ASSET_REGISTRY` plus the top 500
tokens by market cap from CoinGecko.  Static registry entries always take
priority — CoinGecko only fills gaps for tokens outside the Big 5 umbrellas.

Usage::

    from app.services.token_universe import get_token_universe, get_price_service

    universe = get_token_universe()
    await universe.refresh()          # called once at startup + every 24h
    asset = universe.get_token("UNI") # -> AssetDefinition | None

    prices = get_price_service()
    btc = await prices.get_price("BTC")  # -> 65432.10
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from typing import Any

import structlog

from asset_registry import (
    ASSET_REGISTRY,
    AssetDefinition,
    AssetSubType,
    AssetUmbrella,
    Chain,
    FungibilityTier,
)

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# CoinGecko platform → Chain mapping
# ---------------------------------------------------------------------------

_PLATFORM_TO_CHAIN: dict[str, Chain] = {
    "ethereum": Chain.ETHEREUM,
    "arbitrum-one": Chain.ARBITRUM,
    "optimistic-ethereum": Chain.OPTIMISM,
    "base": Chain.BASE,
    "polygon-pos": Chain.POLYGON,
    "avalanche": Chain.AVALANCHE,
    "binance-smart-chain": Chain.BSC,
    "solana": Chain.SOLANA,
    "tron": Chain.TRON,
    "sui": Chain.SUI,
    "aptos": Chain.APTOS,
    "sei-network": Chain.SEI,
    "mantle": Chain.MANTLE,
    "scroll": Chain.SCROLL,
    "linea": Chain.LINEA,
    "blast": Chain.BLAST,
    "zksync": Chain.ZKSYNC,
}

# Symbols that are stablecoins (CoinGecko doesn't always classify these)
_KNOWN_STABLES: frozenset[str] = frozenset({
    "USDC", "USDT", "DAI", "BUSD", "TUSD", "USDP", "GUSD", "FRAX",
    "FDUSD", "PYUSD", "USDD", "LUSD", "SUSD", "MIM", "EURC", "EURS",
    "CRVUSD", "GHO", "USDE", "SUSDE", "RLUSD",
})

# Big 5 umbrella detection by CoinGecko ID
_BIG5_CG_IDS: dict[str, AssetUmbrella] = {
    "bitcoin": AssetUmbrella.BTC,
    "ethereum": AssetUmbrella.ETH,
    "solana": AssetUmbrella.SOL,
    "hyperliquid": AssetUmbrella.HYPE,
}

_REFRESH_INTERVAL = 86400  # 24 hours
_PRICE_CACHE_TTL = 300     # 5 minutes
_PRICE_KEY_PREFIX = "yos:price"


# ---------------------------------------------------------------------------
# TokenUniverseService
# ---------------------------------------------------------------------------


class TokenUniverseService:
    """Maintains the top-500 token universe merged with the static registry."""

    def __init__(self) -> None:
        # FULL_TOKEN_UNIVERSE: static registry + dynamic top-500
        self._universe: dict[str, AssetDefinition] = dict(ASSET_REGISTRY)
        # Market cap rank for ordering
        self._ranks: dict[str, int] = {}
        # CoinGecko ID -> canonical ID reverse map
        self._cg_id_map: dict[str, str] = {}
        # Name/symbol search index (lowercase -> canonical_id)
        self._search_index: dict[str, str] = {}
        # Refresh tracking
        self._last_refresh: float = 0.0
        self._lock = asyncio.Lock()
        self._raw_market_data: list[dict] = []

        # Pre-index the static registry
        for asset in ASSET_REGISTRY.values():
            if asset.coingecko_id:
                self._cg_id_map[asset.coingecko_id] = asset.canonical_id
            self._search_index[asset.canonical_id.lower()] = asset.canonical_id
            self._search_index[asset.name.lower()] = asset.canonical_id

    @property
    def is_stale(self) -> bool:
        return (time.monotonic() - self._last_refresh) > _REFRESH_INTERVAL

    @property
    def universe_size(self) -> int:
        return len(self._universe)

    async def refresh(self) -> dict[str, int]:
        """Fetch top 500 from CoinGecko and merge with static registry.

        Returns counts: {static, dynamic, total}.
        """
        async with self._lock:
            return await self._do_refresh()

    async def _do_refresh(self) -> dict[str, int]:
        from app.connectors.coingecko_client import get_client

        client = get_client()
        all_coins: list[dict] = []

        # Fetch 2 pages of 250 to get top 500
        for page in (1, 2):
            try:
                data = await client.coins_markets(
                    ids=[],  # empty = all, ordered by market cap
                    per_page=250,
                    page=page,
                )
                if data:
                    all_coins.extend(data)
            except Exception as exc:
                log.warning(
                    "token_universe_fetch_error",
                    page=page,
                    error=str(exc),
                )

        if not all_coins:
            log.warning("token_universe_no_data")
            return {"static": len(ASSET_REGISTRY), "dynamic": 0, "total": len(self._universe)}

        self._raw_market_data = all_coins
        dynamic_count = 0

        for rank, coin in enumerate(all_coins, start=1):
            cg_id = coin.get("id", "")
            symbol_raw = coin.get("symbol", "").upper()
            name = coin.get("name", symbol_raw)

            if not cg_id or not symbol_raw:
                continue

            # Check if already in static registry (by CoinGecko ID or symbol)
            existing_id = self._cg_id_map.get(cg_id)
            if existing_id and existing_id in ASSET_REGISTRY:
                self._ranks[existing_id] = rank
                continue

            if symbol_raw in ASSET_REGISTRY:
                self._ranks[symbol_raw] = rank
                if cg_id:
                    self._cg_id_map[cg_id] = symbol_raw
                continue

            # Determine umbrella
            umbrella = self._classify_umbrella(cg_id, symbol_raw)

            # Determine sub_type
            sub_type = self._classify_sub_type(symbol_raw, coin)

            # Detect chains from CoinGecko platform data
            chains = self._detect_chains(coin)

            canonical_id = symbol_raw
            asset = AssetDefinition(
                canonical_id=canonical_id,
                name=name,
                umbrella=umbrella,
                sub_type=sub_type,
                fungibility=FungibilityTier.RELATED,
                native_chains=chains,
                coingecko_id=cg_id,
            )

            # Static registry always wins — only add if not present
            if canonical_id not in self._universe or canonical_id not in ASSET_REGISTRY:
                self._universe[canonical_id] = asset
                dynamic_count += 1

            self._ranks[canonical_id] = rank
            self._cg_id_map[cg_id] = canonical_id
            self._search_index[canonical_id.lower()] = canonical_id
            self._search_index[name.lower()] = canonical_id
            self._search_index[cg_id.lower()] = canonical_id

        self._last_refresh = time.monotonic()

        result = {
            "static": len(ASSET_REGISTRY),
            "dynamic": dynamic_count,
            "total": len(self._universe),
        }
        log.info("token_universe_refreshed", **result)
        return result

    def _classify_umbrella(self, cg_id: str, symbol: str) -> AssetUmbrella:
        """Determine umbrella group for a CoinGecko token."""
        # Check known Big 5 IDs
        if cg_id in _BIG5_CG_IDS:
            return _BIG5_CG_IDS[cg_id]
        # Check stablecoins
        if symbol in _KNOWN_STABLES:
            return AssetUmbrella.USD
        return AssetUmbrella.OTHER

    def _classify_sub_type(self, symbol: str, coin: dict) -> AssetSubType:
        """Determine sub_type from CoinGecko metadata."""
        if symbol in _KNOWN_STABLES:
            return AssetSubType.TIER2_STABLE
        # Default for governance/utility tokens
        return AssetSubType.NATIVE_TOKEN

    def _detect_chains(self, coin: dict) -> list[Chain]:
        """Extract chains from CoinGecko platform data if present."""
        # CoinGecko markets endpoint doesn't include platforms,
        # but we can infer from the coin's attributes
        # For now, return empty — the full /coins/{id} endpoint would have this
        return []

    # -- Public lookup API ---------------------------------------------------

    def get_token(self, canonical_id: str) -> AssetDefinition | None:
        """Look up an asset by canonical ID (case-insensitive)."""
        result = self._universe.get(canonical_id)
        if result:
            return result
        return self._universe.get(canonical_id.upper())

    def is_supported(self, canonical_id: str) -> bool:
        """Check if a symbol is in the token universe."""
        return canonical_id in self._universe or canonical_id.upper() in self._universe

    def get_top_n(self, n: int) -> list[AssetDefinition]:
        """Return the top N tokens by market cap rank."""
        ranked = sorted(
            ((self._ranks.get(cid, 9999), cid) for cid in self._universe),
            key=lambda x: x[0],
        )
        return [self._universe[cid] for _, cid in ranked[:n]]

    def get_by_coingecko_id(self, cg_id: str) -> AssetDefinition | None:
        """Look up an asset by its CoinGecko ID."""
        canonical = self._cg_id_map.get(cg_id)
        if canonical:
            return self._universe.get(canonical)
        return None

    def search(self, query: str) -> list[AssetDefinition]:
        """Fuzzy match on name, symbol, coingecko_id."""
        q = query.lower().strip()
        if not q:
            return []

        matches: list[tuple[int, str]] = []
        for key, canonical_id in self._search_index.items():
            if q in key:
                # Exact match scores highest (0), prefix (1), contains (2)
                if key == q:
                    score = 0
                elif key.startswith(q):
                    score = 1
                else:
                    score = 2
                matches.append((score, canonical_id))

        # Deduplicate and sort by score
        seen: set[str] = set()
        results: list[AssetDefinition] = []
        for _, cid in sorted(matches):
            if cid not in seen:
                seen.add(cid)
                asset = self._universe.get(cid)
                if asset:
                    results.append(asset)
            if len(results) >= 20:
                break
        return results

    def get_all_symbols(self) -> frozenset[str]:
        """Return all canonical IDs in the universe as a frozen set."""
        return frozenset(self._universe.keys())

    def get_market_data(self) -> list[dict]:
        """Return the raw CoinGecko market data from last refresh."""
        return self._raw_market_data

    async def sync_to_db(self, db) -> dict[str, int]:
        """Upsert the current universe into the token_universe table.

        Merges static registry data with CoinGecko market data (ranks, prices).
        Static registry entries have is_static=True and always survive upserts.
        Returns {"upserted": N}.
        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from app.models.token_universe import TokenUniverseRow

        now = datetime.now(UTC)

        # Build a fast lookup: coingecko_id -> market data
        cg_data: dict[str, dict] = {}
        for coin in self._raw_market_data:
            if coin.get("id"):
                cg_data[coin["id"]] = coin

        records: list[dict] = []
        for canonical_id, asset in self._universe.items():
            is_static = canonical_id in ASSET_REGISTRY
            rank = self._ranks.get(canonical_id)

            # Get live market data if available
            coin = cg_data.get(asset.coingecko_id or "") if asset.coingecko_id else {}
            price = coin.get("current_price") if coin else None
            market_cap = coin.get("market_cap") if coin else None

            records.append({
                "canonical_id": canonical_id,
                "coingecko_id": asset.coingecko_id,
                "name": asset.name,
                "symbol": canonical_id,
                "umbrella": asset.umbrella.value,
                "sub_type": asset.sub_type.value,
                "market_cap_rank": rank,
                "market_cap_usd": market_cap,
                "current_price_usd": price,
                "price_updated_at": now if price is not None else None,
                "chains": [c.value for c in asset.native_chains],
                "is_static": is_static,
                "last_refreshed_at": now,
            })

        if not records:
            return {"upserted": 0}

        # Upsert in batches of 200
        BATCH = 200
        upserted = 0
        for i in range(0, len(records), BATCH):
            batch = records[i : i + BATCH]
            stmt = pg_insert(TokenUniverseRow).values(batch)
            stmt = stmt.on_conflict_do_update(
                index_elements=["canonical_id"],
                set_={
                    "coingecko_id": stmt.excluded.coingecko_id,
                    "name": stmt.excluded.name,
                    "symbol": stmt.excluded.symbol,
                    "umbrella": stmt.excluded.umbrella,
                    "sub_type": stmt.excluded.sub_type,
                    "market_cap_rank": stmt.excluded.market_cap_rank,
                    "market_cap_usd": stmt.excluded.market_cap_usd,
                    "current_price_usd": stmt.excluded.current_price_usd,
                    "price_updated_at": stmt.excluded.price_updated_at,
                    "chains": stmt.excluded.chains,
                    "is_static": stmt.excluded.is_static,
                    "last_refreshed_at": stmt.excluded.last_refreshed_at,
                    "updated_at": now,
                },
            )
            await db.execute(stmt)
            upserted += len(batch)

        await db.commit()
        log.info("token_universe_synced_to_db", upserted=upserted)
        return {"upserted": upserted}

    async def update_prices_in_db(self, db, market_data: list[dict]) -> int:
        """Update only price/rank columns for tokens already in the DB.

        Called by the 5-minute price refresh job — much cheaper than a full sync.
        Returns number of rows updated.
        """
        from sqlalchemy import update

        now = datetime.now(UTC)
        updated = 0

        for coin in market_data:
            cg_id = coin.get("id", "")
            price = coin.get("current_price")
            market_cap = coin.get("market_cap")
            rank = coin.get("market_cap_rank")

            if price is None:
                continue

            canonical_id = self._cg_id_map.get(cg_id)
            if not canonical_id:
                # Try symbol fallback
                symbol = coin.get("symbol", "").upper()
                if symbol in self._universe:
                    canonical_id = symbol

            if not canonical_id:
                continue

            from app.models.token_universe import TokenUniverseRow

            await db.execute(
                update(TokenUniverseRow)
                .where(TokenUniverseRow.canonical_id == canonical_id)
                .values(
                    current_price_usd=float(price),
                    market_cap_usd=float(market_cap) if market_cap else None,
                    market_cap_rank=rank,
                    price_updated_at=now,
                    updated_at=now,
                )
            )
            updated += 1

        await db.commit()
        return updated


# ---------------------------------------------------------------------------
# PriceService
# ---------------------------------------------------------------------------


class PriceService:
    """Redis-cached USD price lookups.

    Prices are populated by :meth:`update_from_market_data` (called after
    TokenUniverseService refreshes) and read by adapters that need to fill
    ``_usd`` fields.
    """

    def __init__(self) -> None:
        self._local_cache: dict[str, tuple[float, float]] = {}  # symbol -> (price, monotonic_ts)

    async def _get_redis(self):
        try:
            import redis.asyncio as aioredis
            from app.core.config import settings

            r = aioredis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=2.0,
            )
            await r.ping()
            return r
        except Exception:
            return None

    async def update_from_market_data(self, market_data: list[dict]) -> int:
        """Bulk-write prices from CoinGecko market response to Redis.

        Returns the number of prices cached.
        """
        r = await self._get_redis()
        now_iso = datetime.now(UTC).isoformat()
        now_mono = time.monotonic()
        cached = 0

        # Build a CoinGecko ID -> canonical ID map from the universe
        universe = get_token_universe()

        pipe = r.pipeline() if r else None

        for coin in market_data:
            price = coin.get("current_price")
            if price is None:
                continue

            cg_id = coin.get("id", "")
            symbol_raw = coin.get("symbol", "").upper()

            # Resolve to canonical ID
            asset = universe.get_by_coingecko_id(cg_id)
            canonical_id = asset.canonical_id if asset else symbol_raw

            # Update local cache
            self._local_cache[canonical_id] = (float(price), now_mono)

            # Write to Redis
            if pipe:
                key = f"{_PRICE_KEY_PREFIX}:{canonical_id}"
                value = json.dumps({
                    "usd": float(price),
                    "updated_at": now_iso,
                    "coingecko_id": cg_id,
                })
                pipe.set(key, value, ex=_PRICE_CACHE_TTL)
                cached += 1

        if pipe:
            try:
                await pipe.execute()
            except Exception as exc:
                log.debug("price_cache_write_failed", error=str(exc))
            finally:
                await r.aclose()

        log.info("price_cache_updated", cached=cached)
        return cached

    async def get_price(self, canonical_id: str) -> float | None:
        """Get the USD price for a single token."""
        # Check local cache first (faster than Redis)
        local = self._local_cache.get(canonical_id)
        if local and (time.monotonic() - local[1]) < _PRICE_CACHE_TTL:
            return local[0]

        # Fall back to Redis
        r = await self._get_redis()
        if r is None:
            return local[0] if local else None
        try:
            raw = await r.get(f"{_PRICE_KEY_PREFIX}:{canonical_id}")
            if raw:
                data = json.loads(raw)
                price = data["usd"]
                self._local_cache[canonical_id] = (price, time.monotonic())
                return price
        except Exception:
            pass
        finally:
            await r.aclose()

        return local[0] if local else None

    async def get_prices(self, canonical_ids: list[str]) -> dict[str, float]:
        """Get USD prices for multiple tokens."""
        result: dict[str, float] = {}
        missing: list[str] = []
        now = time.monotonic()

        # Check local cache first
        for cid in canonical_ids:
            local = self._local_cache.get(cid)
            if local and (now - local[1]) < _PRICE_CACHE_TTL:
                result[cid] = local[0]
            else:
                missing.append(cid)

        if not missing:
            return result

        # Batch-fetch from Redis
        r = await self._get_redis()
        if r is None:
            return result
        try:
            keys = [f"{_PRICE_KEY_PREFIX}:{cid}" for cid in missing]
            values = await r.mget(*keys)
            for cid, raw in zip(missing, values):
                if raw:
                    data = json.loads(raw)
                    price = data["usd"]
                    result[cid] = price
                    self._local_cache[cid] = (price, now)
        except Exception:
            pass
        finally:
            await r.aclose()

        return result


# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

_universe: TokenUniverseService | None = None
_price_service: PriceService | None = None


def get_token_universe() -> TokenUniverseService:
    """Return the global TokenUniverseService singleton."""
    global _universe
    if _universe is None:
        _universe = TokenUniverseService()
    return _universe


def get_price_service() -> PriceService:
    """Return the global PriceService singleton."""
    global _price_service
    if _price_service is None:
        _price_service = PriceService()
    return _price_service
