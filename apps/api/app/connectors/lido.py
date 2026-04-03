"""
Lido adapter — liquid staking on Ethereum.

Products
────────
stETH — Lido Staked Ether.
        Deposit ETH to Lido and receive stETH (rebasing token).
        Earns Ethereum consensus-layer + execution-layer staking rewards,
        distributed daily as a stETH balance increase.

wstETH is not a separate deposit product — it is a non-rebasing ERC-4626
wrapper around stETH used for DeFi composability.  It is noted in the
receipt-token metadata and tagged in ``composable_venues``.

Exit mechanism
──────────────
Lido implements a withdrawal queue (ERC-7540-adjacent) for unstaking:
  - Submit a withdrawal request to the ``WithdrawalQueue`` contract
  - Wait for finalisation (hours to weeks, depending on queue length)
  - Claim ETH once the batch is finalised

stETH can also be swapped on-chain (Curve stETH/ETH pool, 1inch) without
waiting — but that is secondary liquidity, not tracked here.

Data sources
────────────
All data from Lido's public REST API (``lido_api_url`` setting):

  /v1/protocol/steth/apr/sma
    Seven-day and thirty-day simple-moving-average staking APR.

  /v1/protocol/steth/stats
    Total pooled ETH and USD TVL.

  /v1/protocol/withdrawal-queue/stats
    Unfinalized stETH in the queue; used to estimate queue length.

APR vs APY
──────────
Lido's API returns APR (not APY).  For daily-compounding Ethereum staking
at ~3–4 % the difference is < 0.05 %.  The ``total_apy_pct`` field is
populated with the APR value, labelled "APR (staking, approximate APY)".

Supported chains: ETHEREUM.
Refresh interval: 3600 s — staking rate is stable and changes slowly.
"""
from __future__ import annotations

import asyncio
from typing import Any

import structlog

from app.connectors.base_adapter import ProtocolAdapter
from app.connectors.http_client import get_json
from app.core.config import settings
from asset_registry import Chain, Venue
from opportunity_schema import (
    EffectiveDuration,
    LiquidityInfo,
    MarketOpportunity,
    OpportunitySide,
    OpportunityType,
    ReceiptTokenInfo,
    RewardBreakdown,
    RewardType,
)

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# API endpoints — relative to lido_api_url base
# ---------------------------------------------------------------------------

_APR_PATH = "/v1/protocol/steth/apr/sma"
_STATS_PATH = "/v1/protocol/steth/stats"
_QUEUE_PATH = "/v1/protocol/withdrawal-queue/stats"

# Daily ETH entering the queue is bounded by Lido's daily withdrawal limit.
# We use total staked ETH to normalise the unfinalized stETH into an
# approximate wait-time estimate (staked_eth × daily_yield ≈ daily churn).
_APPROX_DAILY_CHURN_RATE = 0.0001  # ~0.01 % of staked ETH per day withdrawable


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _estimate_queue_days(
    unfinalized_steth: float | None,
    total_staked_eth: float | None,
) -> float | None:
    """Rough estimate of queue length in days.

    Uses the daily churn rate heuristic: on a typical day Lido can finalise
    approximately ``total_staked × 0.01 %`` worth of withdrawals.  This is a
    rough lower-bound; the actual rate depends on validator exits.
    """
    if unfinalized_steth is None or total_staked_eth is None:
        return None
    if total_staked_eth <= 0:
        return None
    daily_capacity = total_staked_eth * _APPROX_DAILY_CHURN_RATE
    if daily_capacity <= 0:
        return None
    return round(unfinalized_steth / daily_capacity, 1)


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class LidoAdapter(ProtocolAdapter):
    """Lido stETH liquid-staking adapter — Ethereum only."""

    @property
    def venue(self) -> Venue:
        return Venue.LIDO

    @property
    def protocol_name(self) -> str:
        return "Lido"

    @property
    def protocol_slug(self) -> str:
        return "lido"

    @property
    def supported_chains(self) -> list[Chain]:
        return [Chain.ETHEREUM]

    @property
    def refresh_interval_seconds(self) -> int:
        return 3600

    @property
    def requires_api_key(self) -> bool:
        return False

    @property
    def api_key_env_var(self) -> str | None:
        return None

    def __init__(self) -> None:
        super().__init__()
        # ``lido_api_url`` is added to config when this adapter is integrated.
        # Falls back to the well-known public base URL in the meantime.
        self._api_base: str = getattr(
            settings, "lido_api_url", "https://eth-api.lido.fi"
        ).rstrip("/")

    # -- Fetch -----------------------------------------------------------------

    async def fetch_opportunities(
        self,
        symbols: list[str] | None = None,
        chains: list[Chain] | None = None,
    ) -> list[MarketOpportunity]:
        if chains and Chain.ETHEREUM not in chains:
            return []
        if symbols and "ETH" not in symbols:
            return []

        apr_data, stats_data, queue_data = await asyncio.gather(
            self._fetch_apr(),
            self._fetch_stats(),
            self._fetch_queue_stats(),
            return_exceptions=True,
        )

        if isinstance(apr_data, Exception):
            log.warning("lido_apr_fetch_error", error=str(apr_data))
            return []

        apy_pct = self._extract_apr(apr_data)
        if apy_pct is None:
            log.warning("lido_apr_missing")
            return []

        tvl_usd, total_staked_eth = self._extract_stats(
            stats_data if not isinstance(stats_data, Exception) else {}
        )

        liquidity = self._build_liquidity(
            queue_data if not isinstance(queue_data, Exception) else {},
            total_staked_eth=total_staked_eth,
            tvl_usd=tvl_usd,
        )

        opp = self._build_steth_opportunity(
            apy_pct=apy_pct,
            tvl_usd=tvl_usd,
            total_staked_eth=total_staked_eth,
            liquidity=liquidity,
        )

        log.info("lido_fetch_done", apy_pct=apy_pct, tvl_usd=tvl_usd)
        return [opp]

    # -- API calls -------------------------------------------------------------

    async def _fetch_apr(self) -> dict:
        return await get_json(f"{self._api_base}{_APR_PATH}")

    async def _fetch_stats(self) -> dict:
        return await get_json(f"{self._api_base}{_STATS_PATH}")

    async def _fetch_queue_stats(self) -> dict:
        return await get_json(f"{self._api_base}{_QUEUE_PATH}")

    # -- Parsing ---------------------------------------------------------------

    def _extract_apr(self, data: dict) -> float | None:
        """
        Extract staking APR from the SMA response.

        Lido returns a JSON envelope: ``{"data": {"smaApr": 3.5, ...}}``.
        We prefer the 7-day SMA.  Field names are tried in priority order to
        handle minor API version differences.
        """
        inner = data.get("data") or data  # unwrap {"data": {...}} envelope
        for key in ("smaApr", "7dSmaApr", "averageApr", "apr"):
            val = _safe_float(inner.get(key))
            if val is not None and val > 0:
                return val
        return None

    def _extract_stats(
        self,
        data: dict,
    ) -> tuple[float | None, float | None]:
        """Return (tvl_usd, total_staked_eth) from the stats endpoint."""
        inner = data.get("data") or data
        tvl_usd = _safe_float(inner.get("totalStakedUsd")) or _safe_float(
            inner.get("marketCap")
        )
        # totalStaked may be raw ETH (float) or Wei (large int string)
        total_staked_raw = inner.get("totalStaked")
        total_staked_eth: float | None = None
        if total_staked_raw is not None:
            val = _safe_float(total_staked_raw)
            if val is not None:
                # Heuristic: if value > 1e15 it is likely in Wei
                total_staked_eth = val / 1e18 if val > 1e15 else val
        return tvl_usd, total_staked_eth

    def _build_liquidity(
        self,
        queue_data: dict,
        total_staked_eth: float | None,
        tvl_usd: float | None,
    ) -> LiquidityInfo:
        """Build LiquidityInfo with withdrawal-queue details."""
        inner = queue_data.get("data") or queue_data

        # unfinalizedStETH may be in Wei (large int string) or ETH (float)
        unfinalized_raw = inner.get("unfinalizedStETH")
        unfinalized_eth: float | None = None
        if unfinalized_raw is not None:
            val = _safe_float(unfinalized_raw)
            if val is not None:
                unfinalized_eth = val / 1e18 if val > 1e15 else val

        queue_days = _estimate_queue_days(unfinalized_eth, total_staked_eth)

        return LiquidityInfo(
            has_lockup=False,
            has_withdrawal_queue=True,
            current_queue_length_days=queue_days,
            available_liquidity_usd=tvl_usd,
            notes=(
                "Withdrawal via Lido queue (hours–weeks depending on demand). "
                "Instant exit available on Curve stETH/ETH pool at a small premium."
            ),
        )

    # -- Opportunity builder ---------------------------------------------------

    def _build_steth_opportunity(
        self,
        *,
        apy_pct: float,
        tvl_usd: float | None,
        total_staked_eth: float | None,
        liquidity: LiquidityInfo,
    ) -> MarketOpportunity:
        receipt = ReceiptTokenInfo(
            produces_receipt_token=True,
            receipt_token_id="stETH",
            receipt_token_symbol="stETH",
            is_transferable=True,
            is_composable=True,
            composable_venues=["Aave V3", "SparkLend", "Morpho Blue", "Compound V3"],
            notes=(
                "stETH is a rebasing token — balance increases daily with staking rewards. "
                "Wrap to wstETH (ERC-4626, non-rebasing) for use as collateral in Aave V3, "
                "SparkLend, Morpho Blue, and Compound V3."
            ),
        )

        rewards = [
            RewardBreakdown(
                reward_type=RewardType.NATIVE_YIELD,
                apy_pct=apy_pct,
                is_variable=True,
                notes="ETH staking APR via Lido (7-day SMA; consensus + execution rewards)",
            ),
        ]

        return self.build_opportunity(
            asset_id="ETH",
            asset_symbol="ETH",
            chain=Chain.ETHEREUM.value,
            market_id="lido:steth:ethereum",
            market_name="Lido stETH",
            side=OpportunitySide.SUPPLY,
            opportunity_type=OpportunityType.STAKING,
            effective_duration=EffectiveDuration.VARIABLE,
            total_apy_pct=apy_pct,
            base_apy_pct=apy_pct,
            reward_breakdown=rewards,
            total_supplied=total_staked_eth,
            total_supplied_usd=tvl_usd,
            tvl_usd=tvl_usd,
            # No supply cap — Lido accepts unlimited ETH (validator queue is separate)
            is_capacity_capped=False,
            liquidity=liquidity,
            is_collateral_eligible=False,
            receipt_token=receipt,
            tags=["liquid-staking", "lido", "eigenlayer-eligible"],
            source_url="https://stake.lido.fi",
        )

    # -- Health check ----------------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        try:
            data = await get_json(f"{self._api_base}{_APR_PATH}")
            apy = self._extract_apr(data)
            return {
                "status": "ok" if apy is not None else "degraded",
                "last_success": self._last_success,
                "error": None,
            }
        except Exception as exc:
            return {
                "status": "down",
                "last_success": self._last_success,
                "error": str(exc),
            }
