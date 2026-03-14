"""
Kamino Finance connector — reads lending market risk parameters.

Endpoints (public REST, no API key required):
  GET {base}/v2/kamino-market/all              → list of all markets
  GET {base}/v2/kamino-market/{address}/reserves → reserves for one market

Field mapping (Kamino raw → normalized):
  reserve.config.loanToValueRatio              → max_ltv   (decimal 0-1)
  reserve.config.liquidationThreshold          → liquidation_threshold (decimal 0-1)
  reserve.config.liquidationBonus              → liquidation_penalty   (decimal 0-1)
  reserve.config.borrowLimit                   → borrow_cap_native
  reserve.config.depositLimit                  → supply_cap_native
  reserve.borrowedAmount (liquidity)           → available_capacity_native proxy
  reserve.liquidity.availableAmount            → available_capacity_native
  reserve.config.status ("Active")             → is_active, borrowing_enabled
  reserve.liquidity.mintAddress (symbol via lookup) → asset

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


class KaminoReserveConfig(BaseModel):
    loan_to_value_ratio: float | None = Field(None, alias="loanToValueRatio")
    liquidation_threshold: float | None = Field(None, alias="liquidationThreshold")
    liquidation_bonus: float | None = Field(None, alias="liquidationBonus")
    borrow_limit: float | None = Field(None, alias="borrowLimit")
    deposit_limit: float | None = Field(None, alias="depositLimit")
    status: str | None = None

    model_config = {"extra": "allow", "populate_by_name": True}


class KaminoLiquidity(BaseModel):
    mint_pubkey: str | None = Field(None, alias="mintPubkey")
    available_amount: float | None = Field(None, alias="availableAmount")
    mint_decimals: int | None = Field(None, alias="mintDecimals")

    model_config = {"extra": "allow", "populate_by_name": True}


class KaminoReserve(BaseModel):
    """One reserve from GET /v2/kamino-market/{address}/reserves."""

    address: str | None = None
    symbol: str | None = None  # provided directly by the API if available
    config: KaminoReserveConfig | None = None
    liquidity: KaminoLiquidity | None = None

    model_config = {"extra": "allow"}

    @property
    def is_active(self) -> bool:
        return (self.config is not None and
                (self.config.status or "").lower() == "active")

    @property
    def available_capacity_native(self) -> float | None:
        return self.liquidity.available_amount if self.liquidity else None


class KaminoMarket(BaseModel):
    """One entry from GET /v2/kamino-market/all."""

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
        data = await self._get("/v2/kamino-market/all")
        markets_raw: list[dict] = data if isinstance(data, list) else data.get("markets", [])
        markets = [KaminoMarket.model_validate(m) for m in markets_raw]
        log.info("kamino_fetch_markets_done", count=len(markets))
        return markets

    async def fetch_reserves(self, market_address: str) -> list[KaminoReserve]:
        log.debug("kamino_fetch_reserves", market=market_address)
        data = await self._get(f"/v2/kamino-market/{market_address}/reserves")
        reserves_raw: list[dict] = data if isinstance(data, list) else data.get("reserves", [])
        return [KaminoReserve.model_validate(r) for r in reserves_raw]
