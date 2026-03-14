"""
Kamino Finance connector — reads lending market risk parameters.

Endpoints (public REST, no API key required):
  GET {base}/v2/kamino-market?env=mainnet-beta       → list of all markets
  GET {base}/kamino-market/{address}/reserves/metrics → reserve metrics per market

Field mapping (Kamino raw → normalized):
  maxLtv           → max_ltv   (decimal string, e.g. "0.8")
  liquidityToken   → asset (symbol)
  totalSupplyUsd   → supply cap proxy (USD)
  totalBorrowUsd   → borrow cap proxy (USD)
  totalSupply - totalBorrow → available_capacity (native units)

Note: Kamino uses decimal fractions (0.80) natively — no conversion needed.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog
from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

log = structlog.get_logger(__name__)

_TIMEOUT = 20.0
_RETRIES = 3


class KaminoReserveMetrics(BaseModel):
    """One entry from GET /kamino-market/{address}/reserves/metrics."""

    reserve: str  # reserve pubkey
    liquidity_token: str = Field(alias="liquidityToken")
    liquidity_token_mint: str | None = Field(None, alias="liquidityTokenMint")
    max_ltv: str | None = Field(None, alias="maxLtv")      # decimal string e.g. "0.8"
    borrow_apy: float | None = Field(None, alias="borrowApy")
    supply_apy: float | None = Field(None, alias="supplyApy")
    total_supply: str | None = Field(None, alias="totalSupply")
    total_borrow: str | None = Field(None, alias="totalBorrow")
    total_borrow_usd: float | None = Field(None, alias="totalBorrowUsd")
    total_supply_usd: float | None = Field(None, alias="totalSupplyUsd")

    model_config = {"extra": "allow", "populate_by_name": True}

    @property
    def symbol(self) -> str:
        return self.liquidity_token.upper()

    @property
    def max_ltv_float(self) -> float | None:
        try:
            return float(self.max_ltv) if self.max_ltv else None
        except ValueError:
            return None

    @property
    def available_capacity_usd(self) -> float | None:
        if self.total_supply_usd is not None and self.total_borrow_usd is not None:
            return max(0.0, self.total_supply_usd - self.total_borrow_usd)
        return None


# Keep KaminoReserve as an alias for backwards compatibility with ingestion code
KaminoReserve = KaminoReserveMetrics


class KaminoMarket(BaseModel):
    """One entry from GET /v2/kamino-market?env=mainnet-beta."""

    lending_market: str = Field(alias="lendingMarket")
    name: str | None = None

    model_config = {"extra": "allow", "populate_by_name": True}


def _retry():
    return retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        stop=stop_after_attempt(_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )


class KaminoClient:
    """Async REST client for the Kamino Finance public API."""

    def __init__(self, base_url: str) -> None:
        self._base = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "KaminoClient":
        self._client = httpx.AsyncClient(timeout=_TIMEOUT)
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()

    @_retry()
    async def _get(self, path: str) -> Any:
        assert self._client is not None
        resp = await self._client.get(f"{self._base}{path}")
        resp.raise_for_status()
        return resp.json()

    async def fetch_markets(self) -> list[KaminoMarket]:
        log.info("kamino_fetch_markets_start")
        data = await self._get("/v2/kamino-market?env=mainnet-beta")
        markets_raw: list[dict] = data if isinstance(data, list) else data.get("markets", [])
        markets = [KaminoMarket.model_validate(m) for m in markets_raw]
        log.info("kamino_fetch_markets_done", count=len(markets))
        return markets

    async def fetch_reserves(self, market_address: str) -> list[KaminoReserveMetrics]:
        log.debug("kamino_fetch_reserves", market=market_address)
        data = await self._get(f"/kamino-market/{market_address}/reserves/metrics")
        reserves_raw: list[dict] = data if isinstance(data, list) else data.get("reserves", [])
        return [KaminoReserveMetrics.model_validate(r) for r in reserves_raw]
