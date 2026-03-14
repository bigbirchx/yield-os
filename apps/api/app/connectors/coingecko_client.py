"""
CoinGecko Pro API client.

Used as the canonical MARKET REFERENCE and ASSET METADATA layer for Yield OS.
It is NOT the source of truth for protocol-native lending parameters, LTVs,
or derivatives routing — those come from DeFiLlama, Aave, Morpho, Kamino,
and Velo respectively.

Auth
----
  If COINGECKO_API_KEY is set  → Pro endpoint (pro-api.coingecko.com)
                                  with x-cg-pro-api-key header.
  If not set                   → Public endpoint (api.coingecko.com)
                                  with no auth header (free tier).

Retry
-----
  tenacity: 3 attempts, exponential backoff 1s…10s.
  Retries on: 429 (rate limit), 500/502/503/504 (transient).
  Does NOT retry on 401/403 (auth error) — raises immediately.
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

_PRO_BASE = "https://pro-api.coingecko.com/api/v3"
_FREE_BASE = "https://api.coingecko.com/api/v3"
_TIMEOUT = 10.0
_MAX_RETRIES = 3


def _should_retry(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(exc, (httpx.TimeoutException, httpx.ConnectError))


class CoinGeckoClient:
    """
    Async CoinGecko client.  Instantiate once and reuse.
    Uses the Pro endpoint when COINGECKO_API_KEY is configured,
    otherwise falls back to the free public endpoint.
    """

    def __init__(self) -> None:
        self._api_key = settings.coingecko_api_key
        self._base = _PRO_BASE if self._api_key else _FREE_BASE
        self._headers: dict[str, str] = {}
        if self._api_key:
            self._headers["x-cg-pro-api-key"] = self._api_key

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
        ids: list[str],
        vs_currency: str = "usd",
        per_page: int = 100,
        page: int = 1,
    ) -> list[dict]:
        """
        GET /coins/markets
        Returns current price, market cap, volume, etc. for the given IDs.
        """
        try:
            return await self._get(
                "/coins/markets",
                params={
                    "vs_currency": vs_currency,
                    "ids": ",".join(ids),
                    "per_page": per_page,
                    "page": page,
                    "sparkline": "false",
                    "price_change_percentage": "24h",
                },
            )
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
