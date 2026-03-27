"""
DeFiLlama connectors — free-tier only.

Three base URLs are used:
  https://yields.llama.fi   — yield pools (/pools, /chart/{pool_id})
  https://api.llama.fi      — protocols, chains, prices, DEX/OI/fees
  https://stablecoins.llama.fi — stablecoin ecosystem data

NO pro-api.llama.fi endpoints are used. No API key is required.

Free endpoints in scope
-----------------------
yields.llama.fi
  GET /pools                                  all yield pool snapshots
  GET /chart/{pool_id}                        historical APY/TVL for one pool

api.llama.fi
  GET /protocols                              all protocols with TVL
  GET /protocol/{protocol}                    protocol detail + TVL history
  GET /tvl/{protocol}                         single protocol current TVL
  GET /v2/chains                              all chains with current TVL
  GET /v2/historicalChainTvl/{chain}          chain TVL time-series
  GET /overview/dexs                          DEX volume overview
  GET /overview/open-interest                 perp open-interest overview
  GET /overview/fees                          fees & revenue overview
  GET /summary/dexs/{protocol}               single DEX volume detail

stablecoins.llama.fi
  GET /stablecoins                            all stablecoins with circulating
  GET /stablecoinchains                       per-chain aggregate stats
  GET /stablecoincharts/all                   aggregate daily circulating history
  GET /stablecoincharts/{chain}               per-chain daily circulating history
  GET /stablecoin/{id}                        single stablecoin detail + history

Explicitly NOT implemented (Pro-only)
--------------------------------------
  /yields/poolsBorrow, /yields/chartLendBorrow, /yields/lsdRates,
  /yields/perps, unlocks, token liquidity, active users.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog
from pydantic import BaseModel, Field
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = structlog.get_logger(__name__)

_YIELDS_BASE      = "https://yields.llama.fi"
_MAIN_BASE        = "https://api.llama.fi"
_STABLES_BASE     = "https://stablecoins.llama.fi"
_DEFAULT_TIMEOUT  = 30.0
_RETRY_ATTEMPTS   = 3


# ---------------------------------------------------------------------------
# Retry decorator
# ---------------------------------------------------------------------------

def _retry():
    return retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        stop=stop_after_attempt(_RETRY_ATTEMPTS),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )


# ---------------------------------------------------------------------------
# Response models — yields
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
    stablecoin: bool = False
    il_risk: str | None = Field(None, alias="ilRisk")
    exposure: str | None = None
    predictions: dict | None = None

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
# Response models — protocols / chains
# ---------------------------------------------------------------------------

class DeFiLlamaProtocol(BaseModel):
    """One item from GET /protocols."""
    id: str = ""
    name: str = ""
    slug: str = Field("", alias="slug")
    symbol: str | None = None
    category: str | None = None
    chain: str | None = None
    chains: list[str] = []
    tvl: float | None = None
    change_1d: float | None = Field(None, alias="change_1d")
    change_7d: float | None = Field(None, alias="change_7d")
    change_1m: float | None = Field(None, alias="change_1m")
    mcap: float | None = None

    model_config = {"extra": "allow", "populate_by_name": True}


class DeFiLlamaChain(BaseModel):
    """One item from GET /v2/chains."""
    name: str = ""
    tvl: float | None = None
    token_symbol: str | None = Field(None, alias="tokenSymbol")
    gecko_id: str | None = Field(None, alias="gecko_id")

    model_config = {"extra": "allow", "populate_by_name": True}


class DeFiLlamaChainTvlPoint(BaseModel):
    """One point from GET /v2/historicalChainTvl/{chain}."""
    date: int  # unix timestamp
    tvl: float

    model_config = {"extra": "allow"}


# ---------------------------------------------------------------------------
# Response models — stablecoins
# ---------------------------------------------------------------------------

class DeFiLlamaStablecoin(BaseModel):
    """One item from GET /stablecoins peggedAssets list."""
    id: str = ""
    name: str = ""
    symbol: str = ""
    gecko_id: str | None = Field(None, alias="gecko_id")
    peg_type: str | None = Field(None, alias="pegType")
    peg_mechanism: str | None = Field(None, alias="pegMechanism")
    circulating: dict | None = None
    circulating_prev_day: dict | None = Field(None, alias="circulatingPrevDay")
    chain_circulating: dict | None = Field(None, alias="chainCirculating")

    model_config = {"extra": "allow", "populate_by_name": True}

    @property
    def circulating_usd(self) -> float | None:
        if self.circulating:
            return self.circulating.get("peggedUSD")
        return None


class DeFiLlamaStablecoinHistPoint(BaseModel):
    """One point from GET /stablecoincharts/* ."""
    date: int  # unix timestamp
    total_circulating: dict | None = Field(None, alias="totalCirculating")
    total_circulating_usd: dict | None = Field(None, alias="totalCirculatingUSD")

    model_config = {"extra": "allow", "populate_by_name": True}

    @property
    def circulating_usd(self) -> float | None:
        if self.total_circulating_usd:
            return self.total_circulating_usd.get("peggedUSD")
        return None


# ---------------------------------------------------------------------------
# Response models — DEX / OI / Fees overview
# ---------------------------------------------------------------------------

class DeFiLlamaProtocolVolume(BaseModel):
    """One protocol entry from /overview/dexs or /overview/open-interest."""
    name: str = ""
    slug: str | None = Field(None, alias="module")
    total_24h: float | None = Field(None, alias="total24h")
    total_7d: float | None = Field(None, alias="total7d")
    total_30d: float | None = Field(None, alias="total30d")
    chains: list[str] = []

    model_config = {"extra": "allow", "populate_by_name": True}


class DeFiLlamaOverviewResp(BaseModel):
    """Top-level response from /overview/dexs, /overview/open-interest, /overview/fees."""
    total_24h: float | None = Field(None, alias="total24h")
    total_48h_to_24h: float | None = Field(None, alias="total48hto24h")
    protocols: list[DeFiLlamaProtocolVolume] = []

    model_config = {"extra": "allow", "populate_by_name": True}


# ---------------------------------------------------------------------------
# Yields client (yields.llama.fi)
# ---------------------------------------------------------------------------

class DeFiLlamaClient:
    """
    Async HTTP client for yields.llama.fi — pool snapshots and history.
    No API key required.
    """

    def __init__(self, api_key: str = "", base_url: str = _YIELDS_BASE) -> None:
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
        log.debug("defillama_yields_request", path=path)
        resp = await self._client.get(path, params=params or {})
        resp.raise_for_status()
        return resp.json()

    async def fetch_pools(self) -> list[DeFiLlamaPool]:
        """Fetch all current yield/lending/staking pools."""
        data = await self._get("/pools")
        raw: list[dict] = data.get("data", data) if isinstance(data, dict) else data
        log.info("defillama_pools_fetched", count=len(raw))
        return [DeFiLlamaPool.model_validate(p) for p in raw]

    async def fetch_pool_chart(self, pool_id: str) -> list[DeFiLlamaHistoryPoint]:
        """Fetch daily historical APY/TVL for one pool (by DeFiLlama UUID)."""
        data = await self._get(f"/chart/{pool_id}")
        raw: list[dict] = data.get("data", []) if isinstance(data, dict) else data
        log.debug("defillama_chart_fetched", pool_id=pool_id, points=len(raw))
        return [DeFiLlamaHistoryPoint.model_validate(p) for p in raw]


# ---------------------------------------------------------------------------
# Main client (api.llama.fi)
# ---------------------------------------------------------------------------

class DeFiLlamaMainClient:
    """
    Async HTTP client for api.llama.fi — protocols, chains, prices, DEX/OI/fees.
    No API key required.
    """

    def __init__(self, base_url: str = _MAIN_BASE) -> None:
        self._base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "DeFiLlamaMainClient":
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=_DEFAULT_TIMEOUT,
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()

    @_retry()
    async def _get(self, path: str, params: dict | None = None) -> Any:
        assert self._client is not None
        log.debug("defillama_main_request", path=path)
        resp = await self._client.get(path, params=params or {})
        resp.raise_for_status()
        return resp.json()

    async def fetch_protocols(self) -> list[DeFiLlamaProtocol]:
        """GET /protocols — all protocols with TVL, change metrics."""
        raw: list[dict] = await self._get("/protocols")
        log.info("defillama_protocols_fetched", count=len(raw))
        result = []
        for item in raw:
            try:
                # DefiLlama uses 'slug' field inconsistently; derive it from id if absent
                if "slug" not in item:
                    item["slug"] = item.get("slug") or item.get("id", "")
                result.append(DeFiLlamaProtocol.model_validate(item))
            except Exception:
                pass
        return result

    async def fetch_protocol(self, slug: str) -> dict:
        """GET /protocol/{protocol} — detail + historical TVL breakdown."""
        data = await self._get(f"/protocol/{slug}")
        log.debug("defillama_protocol_fetched", slug=slug)
        return data if isinstance(data, dict) else {}

    async def fetch_protocol_tvl(self, slug: str) -> float | None:
        """GET /tvl/{protocol} — single current TVL scalar."""
        try:
            val = await self._get(f"/tvl/{slug}")
            return float(val) if val is not None else None
        except Exception:
            return None

    async def fetch_chains(self) -> list[DeFiLlamaChain]:
        """GET /v2/chains — all chains with current TVL."""
        raw: list[dict] = await self._get("/v2/chains")
        log.info("defillama_chains_fetched", count=len(raw))
        return [DeFiLlamaChain.model_validate(c) for c in raw]

    async def fetch_chain_tvl_history(self, chain: str) -> list[DeFiLlamaChainTvlPoint]:
        """GET /v2/historicalChainTvl/{chain} — daily TVL history for one chain."""
        raw: list[dict] = await self._get(f"/v2/historicalChainTvl/{chain}")
        log.debug("defillama_chain_tvl_fetched", chain=chain, points=len(raw))
        return [DeFiLlamaChainTvlPoint.model_validate(p) for p in raw]

    async def fetch_overview_dexs(self) -> DeFiLlamaOverviewResp:
        """GET /overview/dexs — aggregate + per-protocol DEX volume."""
        data = await self._get(
            "/overview/dexs",
            params={
                "excludeTotalDataChart": "true",
                "excludeTotalDataChartBreakdown": "true",
                "dataType": "dailyVolume",
            },
        )
        protocols = [
            DeFiLlamaProtocolVolume.model_validate(p)
            for p in data.get("protocols", [])
        ]
        return DeFiLlamaOverviewResp(
            total24h=data.get("total24h"),
            total48hto24h=data.get("total48hto24h"),
            protocols=protocols,
        )

    async def fetch_overview_open_interest(self) -> DeFiLlamaOverviewResp:
        """GET /overview/open-interest — aggregate + per-protocol OI."""
        data = await self._get(
            "/overview/open-interest",
            params={
                "excludeTotalDataChart": "true",
                "excludeTotalDataChartBreakdown": "true",
            },
        )
        protocols = [
            DeFiLlamaProtocolVolume.model_validate(p)
            for p in data.get("protocols", [])
        ]
        return DeFiLlamaOverviewResp(
            total24h=data.get("total24h"),
            total48hto24h=data.get("total48hto24h"),
            protocols=protocols,
        )

    async def fetch_overview_fees(self) -> DeFiLlamaOverviewResp:
        """GET /overview/fees — aggregate + per-protocol fees & revenue."""
        data = await self._get(
            "/overview/fees",
            params={
                "excludeTotalDataChart": "true",
                "excludeTotalDataChartBreakdown": "true",
                "dataType": "dailyFees",
            },
        )
        protocols = [
            DeFiLlamaProtocolVolume.model_validate(p)
            for p in data.get("protocols", [])
        ]
        return DeFiLlamaOverviewResp(
            total24h=data.get("total24h"),
            total48hto24h=data.get("total48hto24h"),
            protocols=protocols,
        )

    async def fetch_dex_summary(self, protocol: str) -> dict:
        """GET /summary/dexs/{protocol} — single DEX volume detail."""
        try:
            return await self._get(f"/summary/dexs/{protocol}", params={"dataType": "dailyVolume"})
        except httpx.HTTPStatusError:
            return {}


# ---------------------------------------------------------------------------
# Stablecoins client (stablecoins.llama.fi)
# ---------------------------------------------------------------------------

class DeFiLlamaStablecoinsClient:
    """
    Async HTTP client for stablecoins.llama.fi — stablecoin ecosystem data.
    No API key required.
    """

    def __init__(self, base_url: str = _STABLES_BASE) -> None:
        self._base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "DeFiLlamaStablecoinsClient":
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=_DEFAULT_TIMEOUT,
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()

    @_retry()
    async def _get(self, path: str, params: dict | None = None) -> Any:
        assert self._client is not None
        log.debug("defillama_stables_request", path=path)
        resp = await self._client.get(path, params=params or {})
        resp.raise_for_status()
        return resp.json()

    async def fetch_stablecoins(self) -> list[DeFiLlamaStablecoin]:
        """GET /stablecoins — all stablecoins with current circulating supply."""
        data = await self._get("/stablecoins")
        assets: list[dict] = data.get("peggedAssets", []) if isinstance(data, dict) else data
        log.info("defillama_stablecoins_fetched", count=len(assets))
        return [DeFiLlamaStablecoin.model_validate(a) for a in assets]

    async def fetch_stablecoin_charts(self, chain: str | None = None) -> list[DeFiLlamaStablecoinHistPoint]:
        """GET /stablecoincharts/all or /stablecoincharts/{chain} — daily aggregate circulating."""
        path = f"/stablecoincharts/{chain}" if chain else "/stablecoincharts/all"
        raw: list[dict] = await self._get(path)
        return [DeFiLlamaStablecoinHistPoint.model_validate(p) for p in raw]

    async def fetch_stablecoin(self, asset_id: str) -> dict:
        """GET /stablecoin/{id} — single stablecoin detail with chain breakdown."""
        try:
            return await self._get(f"/stablecoin/{asset_id}")
        except httpx.HTTPStatusError:
            return {}

    async def fetch_stablecoin_chains(self) -> list[dict]:
        """GET /stablecoinchains — per-chain aggregate stablecoin stats."""
        raw: list[dict] = await self._get("/stablecoinchains")
        return raw
