"""
Aave v3 connector — reads market and reserve data via Aave's official GraphQL API.

Endpoint: https://api.v3.aave.com/graphql  (no API key required, public)

Data sources used, in priority order:
  1. api.v3.aave.com/graphql  — markets list, reserve config, current APY,
                                 borrow/supply caps, liquidity, utilization,
                                 LTV/liquidation params, APY history
  2. UiPoolDataProvider (view contract) — not used in MVP; add only if
     a specific field is absent from the API above

Field mapping (Aave API → normalized):
  underlyingToken.symbol               → asset
  underlyingToken.address              → market_address
  supplyInfo.maxLTV.value              → max_ltv          (decimal, e.g. 0.80)
  supplyInfo.liquidationThreshold.value → liquidation_threshold
  supplyInfo.liquidationBonus.value    → liquidation_penalty
  supplyInfo.canBeCollateral           → collateral_eligible
  supplyInfo.supplyCap.amount.value    → supply_cap_native (token units)
  borrowInfo.borrowCap.amount.value    → borrow_cap_native (token units)
  borrowInfo.borrowingState            → borrowing_enabled ("ENABLED" → True)
  borrowInfo.availableLiquidity.usd    → available_capacity_native (USD proxy)
  !isFrozen && !isPaused               → is_active
  chain.name                           → chain

APY history:
  borrowAPYHistory / supplyAPYHistory  → list of (date, avgRate) samples
  TimeWindow options: LAST_DAY | LAST_WEEK | LAST_MONTH | LAST_SIX_MONTHS | LAST_YEAR
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog
from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

log = structlog.get_logger(__name__)

_API_URL = "https://api.v3.aave.com/graphql"
_TIMEOUT = 20.0
_RETRIES = 3

# Chains we ingest by default. Extend as needed.
DEFAULT_CHAIN_IDS = [
    1,      # Ethereum
    42161,  # Arbitrum
    8453,   # Base
    10,     # Optimism
]

_MARKETS_QUERY = """
query AaveMarkets($chainIds: [ChainId!]!) {
  markets(request: { chainIds: $chainIds }) {
    name
    address
    chain { name chainId }
    reserves {
      underlyingToken { symbol address decimals }
      isFrozen
      isPaused
      supplyInfo {
        apy { value }
        maxLTV { value }
        liquidationThreshold { value }
        liquidationBonus { value }
        canBeCollateral
        supplyCap { usd amount { value } }
        supplyCapReached
      }
      borrowInfo {
        apy { value }
        borrowCap { usd amount { value } }
        borrowCapReached
        availableLiquidity { usd }
        utilizationRate { value }
        borrowingState
      }
    }
  }
}
"""

_APY_HISTORY_QUERY = """
query AaveBorrowAPYHistory(
  $market: EvmAddress!
  $token: EvmAddress!
  $chainId: ChainId!
  $window: TimeWindow!
) {
  borrowAPYHistory(request: {
    market: $market
    underlyingToken: $token
    chainId: $chainId
    window: $window
  }) { avgRate { value } date }

  supplyAPYHistory(request: {
    market: $market
    underlyingToken: $token
    chainId: $chainId
    window: $window
  }) { avgRate { value } date }
}
"""


# ---------------------------------------------------------------------------
# Pydantic models — typed wrappers around the API response
# ---------------------------------------------------------------------------


class AavePercentValue(BaseModel):
    value: str  # decimal string e.g. "0.8000"

    model_config = {"extra": "allow"}

    @property
    def as_float(self) -> float:
        return float(self.value)


class AaveDecimalValue(BaseModel):
    value: str

    model_config = {"extra": "allow"}

    @property
    def as_float(self) -> float:
        return float(self.value)


class AaveTokenAmountValue(BaseModel):
    usd: str | None = None
    amount: AaveDecimalValue | None = None

    model_config = {"extra": "allow"}

    @property
    def amount_float(self) -> float | None:
        return self.amount.as_float if self.amount else None

    @property
    def usd_float(self) -> float | None:
        return float(self.usd) if self.usd else None


class AaveAvailableLiquidity(BaseModel):
    usd: str | None = None

    model_config = {"extra": "allow"}

    @property
    def usd_float(self) -> float | None:
        return float(self.usd) if self.usd else None


class AaveSupplyInfo(BaseModel):
    apy: AavePercentValue
    max_ltv: AavePercentValue = Field(alias="maxLTV")
    liquidation_threshold: AavePercentValue = Field(alias="liquidationThreshold")
    liquidation_bonus: AavePercentValue = Field(alias="liquidationBonus")
    can_be_collateral: bool = Field(alias="canBeCollateral")
    supply_cap: AaveTokenAmountValue = Field(alias="supplyCap")
    supply_cap_reached: bool = Field(alias="supplyCapReached")

    model_config = {"extra": "allow", "populate_by_name": True}


class AaveBorrowInfo(BaseModel):
    apy: AavePercentValue
    borrow_cap: AaveTokenAmountValue = Field(alias="borrowCap")
    borrow_cap_reached: bool = Field(alias="borrowCapReached")
    available_liquidity: AaveAvailableLiquidity = Field(alias="availableLiquidity")
    utilization_rate: AavePercentValue = Field(alias="utilizationRate")
    borrowing_state: str = Field(alias="borrowingState")

    model_config = {"extra": "allow", "populate_by_name": True}

    @property
    def borrowing_enabled(self) -> bool:
        return self.borrowing_state == "ENABLED"


class AaveToken(BaseModel):
    symbol: str
    address: str
    decimals: int

    model_config = {"extra": "allow"}


class AaveReserve(BaseModel):
    """One reserve from the Aave official API."""

    underlying_token: AaveToken = Field(alias="underlyingToken")
    is_frozen: bool = Field(alias="isFrozen")
    is_paused: bool = Field(alias="isPaused")
    supply_info: AaveSupplyInfo = Field(alias="supplyInfo")
    borrow_info: AaveBorrowInfo = Field(alias="borrowInfo")

    # Injected after parsing (from the parent market)
    market_name: str = ""
    market_address: str = ""
    chain_name: str = ""
    chain_id: int = 0

    model_config = {"extra": "allow", "populate_by_name": True}

    @property
    def symbol(self) -> str:
        return self.underlying_token.symbol.upper()

    @property
    def is_active(self) -> bool:
        return not self.is_frozen and not self.is_paused

    @property
    def max_ltv(self) -> float | None:
        v = self.supply_info.max_ltv.as_float
        return v if v > 0 else None

    @property
    def liq_threshold(self) -> float | None:
        v = self.supply_info.liquidation_threshold.as_float
        return v if v > 0 else None

    @property
    def liq_penalty(self) -> float | None:
        v = self.supply_info.liquidation_bonus.as_float
        return v if v > 0 else None

    @property
    def supply_cap_native(self) -> float | None:
        v = self.supply_info.supply_cap.amount_float
        return v if v and v > 0 else None

    @property
    def borrow_cap_native(self) -> float | None:
        v = self.borrow_info.borrow_cap.amount_float
        return v if v and v > 0 else None

    @property
    def available_capacity_usd(self) -> float | None:
        return self.borrow_info.available_liquidity.usd_float


class AaveAPYSample(BaseModel):
    avg_rate: AavePercentValue = Field(alias="avgRate")
    date: str

    model_config = {"extra": "allow", "populate_by_name": True}

    @property
    def rate_float(self) -> float:
        return self.avg_rate.as_float


class AaveAPYHistory(BaseModel):
    borrow: list[AaveAPYSample]
    supply: list[AaveAPYSample]


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


def _retry_dec():
    return retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        stop=stop_after_attempt(_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )


class AaveClient:
    """
    Async GraphQL client for the Aave official API (api.v3.aave.com).

    No API key required. Supports all Aave v3 chains.
    """

    def __init__(self, api_url: str = _API_URL) -> None:
        self._url = api_url
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "AaveClient":
        self._client = httpx.AsyncClient(timeout=_TIMEOUT)
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()

    @_retry_dec()
    async def _graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        assert self._client is not None
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables
        resp = await self._client.post(self._url, json=payload)
        resp.raise_for_status()
        body = resp.json()
        if "errors" in body:
            raise ValueError(f"Aave API error: {body['errors']}")
        return body["data"]

    async def fetch_reserves(
        self,
        chain_ids: list[int] | None = None,
    ) -> list[AaveReserve]:
        """
        Fetch all active reserves across the specified chains.

        Returns one AaveReserve per (market, token) pair, with market_name,
        market_address, chain_name, and chain_id injected for traceability.
        """
        ids = chain_ids or DEFAULT_CHAIN_IDS
        log.info("aave_fetch_reserves_start", chain_ids=ids)

        data = await self._graphql(_MARKETS_QUERY, variables={"chainIds": ids})

        reserves: list[AaveReserve] = []
        for market in data.get("markets", []):
            mname = market["name"]
            maddr = market["address"]
            cname = market["chain"]["name"]
            cid = market["chain"]["chainId"]

            for raw in market.get("reserves", []):
                try:
                    r = AaveReserve.model_validate(raw)
                    r.market_name = mname
                    r.market_address = maddr
                    r.chain_name = cname
                    r.chain_id = cid
                    reserves.append(r)
                except Exception as exc:
                    log.warning(
                        "aave_reserve_parse_error",
                        market=mname,
                        token=raw.get("underlyingToken", {}).get("symbol"),
                        error=str(exc),
                    )

        log.info("aave_fetch_reserves_done", count=len(reserves), markets=len(data.get("markets", [])))
        return reserves

    async def fetch_apy_history(
        self,
        market_address: str,
        token_address: str,
        chain_id: int,
        window: str = "LAST_MONTH",
    ) -> AaveAPYHistory:
        """
        Fetch borrow and supply APY history for a single reserve.

        window: LAST_DAY | LAST_WEEK | LAST_MONTH | LAST_SIX_MONTHS | LAST_YEAR
        """
        data = await self._graphql(
            _APY_HISTORY_QUERY,
            variables={
                "market": market_address,
                "token": token_address,
                "chainId": chain_id,
                "window": window,
            },
        )
        return AaveAPYHistory(
            borrow=[AaveAPYSample.model_validate(s) for s in data.get("borrowAPYHistory", [])],
            supply=[AaveAPYSample.model_validate(s) for s in data.get("supplyAPYHistory", [])],
        )
