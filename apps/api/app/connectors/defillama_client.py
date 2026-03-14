"""
DeFiLlama yields connector.

Base URLs:
  https://yields.llama.fi   — public yields/lending/staking data (no key required)
  https://pro-api.llama.fi  — pro endpoints (requires DEFILLAMA_API_KEY)

Endpoints used:
  GET /pools                -> all current pool snapshots
  GET /chart/{pool_id}      -> historical daily APY/TVL for one pool

The free endpoints work without an API key. If DEFILLAMA_API_KEY is set it is
sent as an Authorization header for access to any pro endpoints added later.

Field mapping (DeFiLlama -> internal):
  project        -> protocol
  symbol         -> symbol  (token symbol, e.g. USDC, WBTC, stETH)
  chain          -> chain
  pool           -> pool_id (UUID)
  tvlUsd         -> tvl_usd
  apyBase        -> supply_apy  (base supply yield, no rewards)
  apyReward      -> reward_supply_apy
  apyBaseBorrow  -> borrow_apy  (variable borrow rate)
  apyRewardBorrow-> reward_borrow_apy
  totalSupplyUsd -> used to compute available_liquidity_usd and utilization
  totalBorrowUsd -> used to compute available_liquidity_usd and utilization
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog
from pydantic import BaseModel, Field, model_validator
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = structlog.get_logger(__name__)

_PUBLIC_BASE = "https://yields.llama.fi"
_DEFAULT_TIMEOUT = 30.0
_RETRY_ATTEMPTS = 3


# ---------------------------------------------------------------------------
# Response shapes
# ---------------------------------------------------------------------------


class DeFiLlamaPool(BaseModel):
    """One row from GET /pools."""

    pool: str = Field(alias="pool")
    chain: str = ""
    project: str = ""
    symbol: str = ""
    tvl_usd: float | None = Field(None, alias="tvlUsd")
    apy: float | None = None
    apy_base: float | None = Field(None, alias="apyBase")
    apy_reward: float | None = Field(None, alias="apyReward")
    apy_base_borrow: float | None = Field(None, alias="apyBaseBorrow")
    apy_reward_borrow: float | None = Field(None, alias="apyRewardBorrow")
    total_supply_usd: float | None = Field(None, alias="totalSupplyUsd")
    total_borrow_usd: float | None = Field(None, alias="totalBorrowUsd")
    ltv: float | None = None
    pool_meta: str | None = Field(None, alias="poolMeta")
    underlying_tokens: list[str] | None = Field(None, alias="underlyingTokens")
    reward_tokens: list[str] | None = Field(None, alias="rewardTokens")

    model_config = {"extra": "allow", "populate_by_name": True}

    @property
    def utilization(self) -> float | None:
        if self.total_supply_usd and self.total_supply_usd > 0 and self.total_borrow_usd is not None:
            return self.total_borrow_usd / self.total_supply_usd
        return None

    @property
    def available_liquidity_usd(self) -> float | None:
        if self.total_supply_usd is not None and self.total_borrow_usd is not None:
            return max(0.0, self.total_supply_usd - self.total_borrow_usd)
        return None


class DeFiLlamaHistoryPoint(BaseModel):
    """One point from GET /chart/{pool_id}."""

    timestamp: str
    tvl_usd: float | None = Field(None, alias="tvlUsd")
    apy: float | None = None
    apy_base: float | None = Field(None, alias="apyBase")
    apy_reward: float | None = Field(None, alias="apyReward")
    apy_base_borrow: float | None = Field(None, alias="apyBaseBorrow")
    il7d: float | None = None

    model_config = {"extra": "allow", "populate_by_name": True}


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


def _retry():
    return retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        stop=stop_after_attempt(_RETRY_ATTEMPTS),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )


class DeFiLlamaClient:
    """
    Async HTTP client for the DeFiLlama yields API.

    Usage:
        async with DeFiLlamaClient(api_key="...") as client:
            pools = await client.fetch_pools()
    """

    def __init__(self, api_key: str = "", base_url: str = _PUBLIC_BASE) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "DeFiLlamaClient":
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=_DEFAULT_TIMEOUT,
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()

    @_retry()
    async def _get(self, path: str, params: dict | None = None) -> Any:
        assert self._client is not None
        log.debug("defillama_request", path=path, params=params)
        resp = await self._client.get(path, params=params or {})
        resp.raise_for_status()
        return resp.json()

    async def fetch_pools(self) -> list[DeFiLlamaPool]:
        """Fetch all current yield/lending/staking pools."""
        data = await self._get("/pools")
        raw_pools: list[dict] = data.get("data", data) if isinstance(data, dict) else data
        log.info("defillama_pools_fetched", count=len(raw_pools))
        return [DeFiLlamaPool.model_validate(p) for p in raw_pools]

    async def fetch_pool_chart(self, pool_id: str) -> list[DeFiLlamaHistoryPoint]:
        """Fetch daily historical APY/TVL for one pool (by DeFiLlama UUID)."""
        data = await self._get(f"/chart/{pool_id}")
        raw_points: list[dict] = data.get("data", []) if isinstance(data, dict) else data
        log.debug("defillama_chart_fetched", pool_id=pool_id, points=len(raw_points))
        return [DeFiLlamaHistoryPoint.model_validate(p) for p in raw_points]
