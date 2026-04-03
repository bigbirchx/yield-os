"""
CoinGecko API client (Demo / Pro / free-public).

Used as the canonical MARKET REFERENCE and ASSET METADATA layer for Yield OS.
It is NOT the source of truth for protocol-native lending parameters, LTVs,
or derivatives routing — those come from DeFiLlama, Aave, Morpho, Kamino,
and Velo respectively.

Auth tiers (auto-detected from the key prefix)
-----------------------------------------------
  Key starts with "CG-"  → Demo key
                            Base URL : https://api.coingecko.com/api/v3
                            Header   : x-cg-demo-api-key
  Any other non-empty key → Pro key
                            Base URL : https://pro-api.coingecko.com/api/v3
                            Header   : x-cg-pro-api-key
  No key set             → Free public tier
                            Base URL : https://api.coingecko.com/api/v3
                            No auth header

Retry
-----
  tenacity: 3 attempts, exponential backoff 1s…10s.
  Retries on: 429 (rate limit), 500/502/503/504 (transient).
  Does NOT retry on 400/401/403 — raises immediately.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings

log = structlog.get_logger(__name__)

_PRO_BASE  = "https://pro-api.coingecko.com/api/v3"
_FREE_BASE = "https://api.coingecko.com/api/v3"
_TIMEOUT = 10.0
_MAX_RETRIES = 3


def _should_retry(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(exc, (httpx.TimeoutException, httpx.ConnectError))


def _is_demo_key(key: str) -> bool:
    """Demo keys are issued by CoinGecko starting with 'CG-'."""
    return key.startswith("CG-")


class CoinGeckoClient:
    """
    Async CoinGecko client.  Instantiate once and reuse.

    Key-tier detection (automatic, no config change needed):
      CG-xxxx  → Demo  → api.coingecko.com + x-cg-demo-api-key
      other    → Pro   → pro-api.coingecko.com + x-cg-pro-api-key
      (none)   → Free  → api.coingecko.com, no header
    """

    def __init__(self) -> None:
        self._api_key = settings.coingecko_api_key
        self._headers: dict[str, str] = {}

        if not self._api_key:
            self._base = _FREE_BASE
            self._tier = "free"
        elif _is_demo_key(self._api_key):
            self._base = _FREE_BASE          # Demo uses the public base URL
            self._headers["x-cg-demo-api-key"] = self._api_key
            self._tier = "demo"
        else:
            self._base = _PRO_BASE
            self._headers["x-cg-pro-api-key"] = self._api_key
            self._tier = "pro"

        log.info("coingecko_client_init", tier=self._tier, base=self._base)

    async def _get(self, path: str, params: dict | None = None) -> Any:
        """Execute a GET with retry/backoff.  Raises on unrecoverable errors."""
        url = f"{self._base}{path}"
        async for attempt in AsyncRetrying(
            retry=retry_if_exception(_should_retry),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            stop=stop_after_attempt(_MAX_RETRIES),
            reraise=True,
        ):
            with attempt:
                async with httpx.AsyncClient(
                    timeout=_TIMEOUT, headers=self._headers
                ) as client:
                    resp = await client.get(url, params=params)
                    resp.raise_for_status()
                    return resp.json()

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def ping(self) -> bool:
        """Return True if CoinGecko API is reachable."""
        try:
            data = await self._get("/ping")
            return bool(data.get("gecko_says"))
        except Exception as exc:
            log.warning("coingecko_ping_failed", error=str(exc))
            return False

    # ------------------------------------------------------------------
    # Coin catalogue
    # ------------------------------------------------------------------

    async def coins_list(self, include_platform: bool = False) -> list[dict]:
        """
        GET /coins/list
        Returns full catalogue: [{id, symbol, name}, ...].
        Large response — cache or call infrequently.
        """
        try:
            return await self._get(
                "/coins/list",
                params={"include_platform": str(include_platform).lower()},
            )
        except Exception as exc:
            log.error("coingecko_coins_list_error", error=str(exc))
            return []

    async def coins_markets(
        self,
        ids: list[str] | None = None,
        vs_currency: str = "usd",
        per_page: int = 100,
        page: int = 1,
        order: str = "market_cap_desc",
    ) -> list[dict]:
        """
        GET /coins/markets
        Returns current price, market cap, volume, etc.

        When *ids* is empty or None, returns coins ordered by *order*
        (default: market_cap_desc) — useful for fetching the top N.
        """
        try:
            params: dict[str, Any] = {
                "vs_currency": vs_currency,
                "order": order,
                "per_page": per_page,
                "page": page,
                "sparkline": "false",
                "price_change_percentage": "24h",
            }
            if ids:
                params["ids"] = ",".join(ids)
            return await self._get("/coins/markets", params=params)
        except Exception as exc:
            log.error("coingecko_coins_markets_error", error=str(exc))
            return []

    # ------------------------------------------------------------------
    # Historical data
    # ------------------------------------------------------------------

    async def market_chart(
        self,
        coin_id: str,
        days: int = 30,
        vs_currency: str = "usd",
    ) -> dict:
        """
        GET /coins/{id}/market_chart
        Returns {prices, market_caps, total_volumes} as [timestamp_ms, value] pairs.
        """
        try:
            return await self._get(
                f"/coins/{coin_id}/market_chart",
                params={
                    "vs_currency": vs_currency,
                    "days": days,
                    "interval": "daily" if days > 90 else "hourly" if days <= 2 else "daily",
                },
            )
        except Exception as exc:
            log.error("coingecko_market_chart_error", coin_id=coin_id, error=str(exc))
            return {}

    async def market_chart_range(
        self,
        coin_id: str,
        from_ts: int,
        to_ts: int,
        vs_currency: str = "usd",
    ) -> dict:
        """
        GET /coins/{id}/market_chart/range
        Explicit unix timestamp range.  Auto-granularity from CoinGecko.
        """
        try:
            return await self._get(
                f"/coins/{coin_id}/market_chart/range",
                params={
                    "vs_currency": vs_currency,
                    "from": from_ts,
                    "to": to_ts,
                },
            )
        except Exception as exc:
            log.error("coingecko_market_chart_range_error", coin_id=coin_id, error=str(exc))
            return {}

    # ------------------------------------------------------------------
    # Global market
    # ------------------------------------------------------------------

    async def global_data(self) -> dict:
        """
        GET /global
        Returns total market cap, 24h volume, BTC dominance, etc.
        """
        try:
            resp = await self._get("/global")
            return resp.get("data", resp)
        except Exception as exc:
            log.error("coingecko_global_error", error=str(exc))
            return {}

    # ------------------------------------------------------------------
    # On-chain token enrichment (Pro)
    # ------------------------------------------------------------------

    async def onchain_tokens(
        self, network: str, addresses: list[str]
    ) -> dict:
        """
        GET /onchain/networks/{network}/tokens/multi/{addresses}
        Pro endpoint — returns token metadata for contract addresses.
        Silently returns {} if no API key is configured.
        """
        if not self._api_key:
            log.debug("coingecko_onchain_skipped", reason="no_api_key")
            return {}
        try:
            addr_str = ",".join(addresses)
            return await self._get(
                f"/onchain/networks/{network}/tokens/multi/{addr_str}"
            )
        except Exception as exc:
            log.error("coingecko_onchain_error", network=network, error=str(exc))
            return {}

    # ------------------------------------------------------------------
    # API key usage (Pro)
    # ------------------------------------------------------------------

    async def api_key_info(self) -> dict:
        """
        GET /key
        Returns plan info and monthly credit usage.
        Only available on Pro — returns {} if no key is set.
        """
        if not self._api_key:
            return {}
        try:
            return await self._get("/key")
        except Exception as exc:
            log.error("coingecko_key_info_error", error=str(exc))
            return {}


# Module-level singleton — import this instead of instantiating per-request.
_client: CoinGeckoClient | None = None


def get_client() -> CoinGeckoClient:
    global _client
    if _client is None:
        _client = CoinGeckoClient()
    return _client
