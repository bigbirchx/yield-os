"""
Morpho Blue connector — reads market risk parameters and borrow rates via the
public GraphQL API.

Endpoint: https://blue-api.morpho.org/graphql  (no API key required)

Field mapping (Morpho raw → normalized):
  lltv (as 1e18 decimal string)  / 1e18  → liquidation_threshold
    (Morpho has one LTV: the LLTV at which liquidation triggers; there is no
     separate "max LTV". For routing purposes we set max_ltv = lltv * 0.95
     as a conservative collateral factor.)
  loanAsset.symbol                         → loan_asset (debt token)
  collateralAsset.symbol                   → asset (collateral)
  uniqueKey                                → market_address
  state.liquidityAssetsUsd                 → available_liquidity_usd
  state.supplyAssetsUsd                    → total_supply_usd
  state.borrowAssetsUsd                    → total_borrow_usd
  state.borrowApy                          → borrow_apy  (decimal, e.g. 0.05 = 5%)
  state.supplyApy                          → supply_apy
  state.utilization                        → utilization (0–1)

Note: Morpho Blue is an isolated-pair model. Each market has exactly one
collateral token and one loan (debt) token. There are no global supply/borrow
caps in the traditional sense — the "cap" is organic market liquidity.
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

_MARKETS_QUERY = """
{
  markets(
    first: 200,
    orderBy: BorrowAssetsUsd,
    orderDirection: Desc,
    where: { borrowApy_lte: 5 }
  ) {
    items {
      uniqueKey
      lltv
      loanAsset {
        symbol
        address
        decimals
      }
      collateralAsset {
        symbol
        address
        decimals
      }
      state {
        supplyAssets
        borrowAssets
        liquidityAssets
        supplyAssetsUsd
        borrowAssetsUsd
        liquidityAssetsUsd
        borrowApy
        supplyApy
        utilization
      }
    }
  }
}
"""


class MorphoToken(BaseModel):
    symbol: str
    address: str
    decimals: int

    model_config = {"extra": "allow"}


class MorphoMarketState(BaseModel):
    supply_assets: int | None = Field(None, alias="supplyAssets")
    borrow_assets: int | None = Field(None, alias="borrowAssets")
    liquidity_assets: int | None = Field(None, alias="liquidityAssets")
    supply_assets_usd: float | None = Field(None, alias="supplyAssetsUsd")
    borrow_assets_usd: float | None = Field(None, alias="borrowAssetsUsd")
    liquidity_assets_usd: float | None = Field(None, alias="liquidityAssetsUsd")
    borrow_apy: float | None = Field(None, alias="borrowApy")
    supply_apy: float | None = Field(None, alias="supplyApy")
    utilization: float | None = Field(None, alias="utilization")

    model_config = {"extra": "allow", "populate_by_name": True}


class MorphoMarket(BaseModel):
    """One market from the Morpho Blue API."""

    unique_key: str = Field(alias="uniqueKey")
    lltv: str  # 1e18-scaled decimal string
    loan_token: MorphoToken = Field(alias="loanAsset")
    collateral_token: MorphoToken = Field(alias="collateralAsset")
    state: MorphoMarketState | None = None

    model_config = {"extra": "allow", "populate_by_name": True}

    @property
    def liquidation_threshold(self) -> float:
        return int(self.lltv) / 1e18

    @property
    def max_ltv(self) -> float:
        """Conservative collateral factor = lltv × 0.95."""
        return self.liquidation_threshold * 0.95

    @property
    def available_capacity_usd(self) -> float | None:
        return self.state.liquidity_assets_usd if self.state else None

    @property
    def borrow_apy(self) -> float | None:
        """Borrow APY as a decimal fraction (e.g. 0.05 = 5%).

        Morpho Blue returns APY as decimal fractions, same as Aave and Kamino.
        The GraphQL query already filters borrowApy_lte:5 (500% max at source).
        """
        if self.state and self.state.borrow_apy is not None:
            val = self.state.borrow_apy
            return val if val < 5.0 else None
        return None

    @property
    def supply_apy(self) -> float | None:
        if self.state and self.state.supply_apy is not None:
            val = self.state.supply_apy
            return val if val < 5.0 else None
        return None

    @property
    def utilization(self) -> float | None:
        return self.state.utilization if self.state else None

    @property
    def total_supply_usd(self) -> float | None:
        return self.state.supply_assets_usd if self.state else None

    @property
    def total_borrow_usd(self) -> float | None:
        return self.state.borrow_assets_usd if self.state else None


def _retry():
    return retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        stop=stop_after_attempt(_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )


class MorphoClient:
    """Async GraphQL client for the Morpho Blue public API."""

    def __init__(self, api_url: str) -> None:
        self._url = api_url
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "MorphoClient":
        self._client = httpx.AsyncClient(timeout=_TIMEOUT)
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()

    @_retry()
    async def _graphql(self, query: str) -> dict[str, Any]:
        assert self._client is not None
        resp = await self._client.post(self._url, json={"query": query})
        resp.raise_for_status()
        body = resp.json()
        if "errors" in body:
            raise ValueError(f"Morpho API error: {body['errors']}")
        return body["data"]

    async def fetch_markets(self) -> list[MorphoMarket]:
        log.info("morpho_fetch_markets_start")
        data = await self._graphql(_MARKETS_QUERY)
        # API returns { markets: { items: [...] } } as of 2025
        items = data.get("markets", {}).get("items", [])
        markets = [MorphoMarket.model_validate(m) for m in items]
        log.info("morpho_fetch_markets_done", count=len(markets))
        return markets
