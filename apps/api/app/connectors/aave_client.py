"""
Aave v3 connector — reads risk parameters via The Graph subgraph.

Subgraph: Aave Protocol v3 on Ethereum
Gateway: https://gateway-arbitrum.network.thegraph.com/api/{key}/subgraphs/id/
         JCNWRypm7FYwV8fx5HhzZPSFaMxgkPuw4TnWm89byKeU

Field mapping (Aave raw → normalized):
  baseLTVasCollateral       / 10000  → max_ltv
  reserveLiquidationThreshold / 10000 → liquidation_threshold
  (reserveLiquidationBonus - 10000)  / 10000 → liquidation_penalty
  borrowCap                           → borrow_cap_native  (in token units, 0 = no cap)
  supplyCap                           → supply_cap_native
  usageAsCollateralEnabled            → collateral_eligible
  borrowingEnabled                    → borrowing_enabled
  isActive                            → is_active
  availableLiquidity                  → available_capacity_native

Note: Aave encodes LTV/threshold in basis points (0-10000). Bonus is encoded as
10000 + penalty_bps (e.g. 10500 = 5% penalty). We convert all to decimals.
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

_RESERVES_QUERY = """
{
  reserves(first: 100, where: { isActive: true }) {
    id
    underlyingAsset
    symbol
    name
    decimals
    isActive
    isFrozen
    borrowingEnabled
    usageAsCollateralEnabled
    baseLTVasCollateral
    reserveLiquidationThreshold
    reserveLiquidationBonus
    borrowCap
    supplyCap
    availableLiquidity
    totalCurrentVariableDebt
    liquidityRate
    variableBorrowRate
  }
}
"""


class AaveReserve(BaseModel):
    """One reserve from the Aave v3 subgraph."""

    id: str
    underlying_asset: str = Field(alias="underlyingAsset")
    symbol: str
    name: str
    decimals: int
    is_active: bool = Field(alias="isActive")
    is_frozen: bool = Field(alias="isFrozen")
    borrowing_enabled: bool = Field(alias="borrowingEnabled")
    usage_as_collateral_enabled: bool = Field(alias="usageAsCollateralEnabled")
    base_ltv_as_collateral: str = Field(alias="baseLTVasCollateral")
    reserve_liquidation_threshold: str = Field(alias="reserveLiquidationThreshold")
    reserve_liquidation_bonus: str = Field(alias="reserveLiquidationBonus")
    borrow_cap: str = Field(alias="borrowCap")
    supply_cap: str = Field(alias="supplyCap")
    available_liquidity: str = Field(alias="availableLiquidity")
    total_current_variable_debt: str = Field(alias="totalCurrentVariableDebt")

    model_config = {"extra": "allow", "populate_by_name": True}

    @property
    def max_ltv(self) -> float | None:
        v = float(self.base_ltv_as_collateral)
        return v / 10_000 if v > 0 else None

    @property
    def liq_threshold(self) -> float | None:
        v = float(self.reserve_liquidation_threshold)
        return v / 10_000 if v > 0 else None

    @property
    def liq_penalty(self) -> float | None:
        # Aave stores bonus as 10000 + penalty_bps; penalty=0 means no liquidation bonus
        bonus = float(self.reserve_liquidation_bonus)
        if bonus <= 10_000:
            return None
        return (bonus - 10_000) / 10_000

    @property
    def borrow_cap_native(self) -> float | None:
        v = float(self.borrow_cap)
        return v if v > 0 else None

    @property
    def supply_cap_native(self) -> float | None:
        v = float(self.supply_cap)
        return v if v > 0 else None

    @property
    def available_capacity_native(self) -> float | None:
        v = float(self.available_liquidity)
        return v / (10 ** self.decimals) if v > 0 else None


def _retry():
    return retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        stop=stop_after_attempt(_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )


class AaveClient:
    """
    Async GraphQL client for the Aave v3 subgraph.

    Requires a free API key from https://thegraph.com (set AAVE_SUBGRAPH_KEY).
    If no key is configured, the connector will be skipped during ingestion.
    """

    def __init__(self, subgraph_url: str) -> None:
        self._url = subgraph_url
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "AaveClient":
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
            raise ValueError(f"Aave subgraph error: {body['errors']}")
        return body["data"]

    async def fetch_reserves(self) -> list[AaveReserve]:
        log.info("aave_fetch_reserves_start")
        data = await self._graphql(_RESERVES_QUERY)
        reserves = [AaveReserve.model_validate(r) for r in data.get("reserves", [])]
        log.info("aave_fetch_reserves_done", count=len(reserves))
        return reserves
