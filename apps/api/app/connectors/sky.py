"""
Sky (formerly MakerDAO) savings-rate adapter.

Products
────────
sDAI  — DAI Savings Rate (DSR).
        Deposit DAI into the MakerDAO Pot contract to earn the DSR.
        The ERC-4626 wrapper ``sDAI`` auto-compounds the rate.
        Instant deposit and withdrawal; no cap; no lock-up.

sUSDS — Sky Savings Rate (SSR).
        Deposit USDS into the Sky protocol to earn the SSR.
        The ERC-4626 wrapper ``sUSDS`` auto-compounds the rate.
        Instant deposit and withdrawal; no cap; no lock-up.

Both wrappers are widely composable: accepted as collateral in SparkLend,
Morpho Blue, Aave V3, and other DeFi protocols.

Data source
───────────
DeFiLlama yields API (``sky_savings_url`` setting).
  - ``project == "maker-dsr"``  → sDAI DSR pool
  - ``project == "sky"``        → sUSDS SSR pool

Matching logic uses project-slug as primary key with a symbol-based fallback.

APY encoding
────────────
DeFiLlama ``apy`` is already a percentage (5.0 = 5 %). No multiplication.

Relationship to SparkAdapter
────────────────────────────
The SparkAdapter (``Venue.SPARK``) bundles DSR/SSR together with SparkLend
lending markets for operational convenience.  This adapter exposes the same
savings products under ``Venue.SKY``, giving downstream consumers a clear
view of the Sky protocol as a standalone yield source independent of
SparkLend's lending book.

Supported chains: ETHEREUM.
Refresh interval: 3600 s — savings rates change via governance, infrequently.
"""
from __future__ import annotations

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
# DeFiLlama project slugs
# ---------------------------------------------------------------------------

_DSR_PROJECT = "maker-dsr"
_SSR_PROJECT = "sky"

# Fallback symbol hints used when the project slug alone isn't sufficient
_DSR_SYMBOL_HINT = "DAI"
_SSR_SYMBOL_HINT = "USDS"

# ---------------------------------------------------------------------------
# Savings product descriptors
# ---------------------------------------------------------------------------

_DSR_CONFIG = dict(
    canonical="DAI",
    asset_symbol="DAI",
    receipt_symbol="sDAI",
    market_id="sky:dsr:sdai",
    market_name="Sky DSR (sDAI)",
    source_url="https://app.sky.money/?widget=savings",
    receipt_notes=(
        "sDAI — ERC-4626 Savings DAI wrapper for the MakerDAO DAI Savings Rate. "
        "Composable as collateral in SparkLend, Morpho Blue, and Aave V3."
    ),
    reward_notes="DAI Savings Rate (DSR) — governance-set, variable",
)

_SSR_CONFIG = dict(
    canonical="USDS",
    asset_symbol="USDS",
    receipt_symbol="sUSDS",
    market_id="sky:ssr:susds",
    market_name="Sky Savings Rate (sUSDS)",
    source_url="https://app.sky.money/?widget=savings",
    receipt_notes=(
        "sUSDS — ERC-4626 Sky Savings Rate wrapper. "
        "Composable as collateral in SparkLend, Morpho Blue, and Aave V3."
    ),
    reward_notes="Sky Savings Rate (SSR) — governance-set, variable",
)


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


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class SkyAdapter(ProtocolAdapter):
    """Sky savings-rate adapter — sDAI (DSR) and sUSDS (SSR) on Ethereum."""

    @property
    def venue(self) -> Venue:
        return Venue.SKY

    @property
    def protocol_name(self) -> str:
        return "Sky"

    @property
    def protocol_slug(self) -> str:
        return "sky"

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
        self._defillama_url = settings.sky_savings_url

    # -- Fetch -----------------------------------------------------------------

    async def fetch_opportunities(
        self,
        symbols: list[str] | None = None,
        chains: list[Chain] | None = None,
    ) -> list[MarketOpportunity]:
        if chains and Chain.ETHEREUM not in chains:
            return []

        data = await get_json(self._defillama_url)
        pools: list[dict] = data.get("data", [])

        # Collect matching pool rows for DSR and SSR
        dsr_pool: dict | None = None
        ssr_pool: dict | None = None

        for pool in pools:
            chain_str = (pool.get("chain") or "").upper()
            if chain_str != "ETHEREUM":
                continue

            project = (pool.get("project") or "").lower()
            symbol_raw = pool.get("symbol", "").upper()

            if dsr_pool is None and (
                project == _DSR_PROJECT
                or ("dsr" in project and _DSR_SYMBOL_HINT in symbol_raw)
            ):
                dsr_pool = pool

            if ssr_pool is None and (
                project == _SSR_PROJECT
                or ("sky" in project and _SSR_SYMBOL_HINT in symbol_raw)
            ):
                ssr_pool = pool

        opportunities: list[MarketOpportunity] = []

        if dsr_pool is not None:
            try:
                opp = self._build_savings_opportunity(dsr_pool, _DSR_CONFIG)
                if opp is not None and (not symbols or opp.asset_id in symbols):
                    opportunities.append(opp)
            except Exception as exc:
                log.warning("sky_dsr_parse_error", error=str(exc))

        if ssr_pool is not None:
            try:
                opp = self._build_savings_opportunity(ssr_pool, _SSR_CONFIG)
                if opp is not None and (not symbols or opp.asset_id in symbols):
                    opportunities.append(opp)
            except Exception as exc:
                log.warning("sky_ssr_parse_error", error=str(exc))

        log.info("sky_fetch_done", total=len(opportunities))
        return opportunities

    # -- Builder ---------------------------------------------------------------

    def _build_savings_opportunity(
        self,
        pool: dict,
        cfg: dict,
    ) -> MarketOpportunity | None:
        apy_pct = _safe_float(pool.get("apy"))
        if apy_pct is None:
            return None

        tvl_usd = _safe_float(pool.get("tvlUsd"))

        receipt = ReceiptTokenInfo(
            produces_receipt_token=True,
            receipt_token_symbol=cfg["receipt_symbol"],
            is_transferable=True,
            is_composable=True,
            composable_venues=["SparkLend", "Morpho Blue", "Aave V3"],
            notes=cfg["receipt_notes"],
        )

        return self.build_opportunity(
            asset_id=cfg["canonical"],
            asset_symbol=cfg["asset_symbol"],
            chain=Chain.ETHEREUM.value,
            market_id=cfg["market_id"],
            market_name=cfg["market_name"],
            side=OpportunitySide.SUPPLY,
            opportunity_type=OpportunityType.SAVINGS,
            effective_duration=EffectiveDuration.OVERNIGHT,
            total_apy_pct=apy_pct,
            base_apy_pct=apy_pct,
            reward_breakdown=[
                RewardBreakdown(
                    reward_type=RewardType.NATIVE_YIELD,
                    apy_pct=apy_pct,
                    is_variable=True,
                    notes=cfg["reward_notes"],
                ),
            ],
            tvl_usd=tvl_usd,
            total_supplied_usd=tvl_usd,
            # No supply cap, no lock-up, instant entry and exit
            is_capacity_capped=False,
            liquidity=LiquidityInfo(
                has_lockup=False,
                available_liquidity_usd=tvl_usd,
                notes="Instant deposit and withdrawal via ERC-4626 wrapper",
            ),
            is_collateral_eligible=False,
            receipt_token=receipt,
            tags=["savings", "maker", "sky"],
            source_url=cfg["source_url"],
        )

    # -- Health check ----------------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        try:
            data = await get_json(self._defillama_url)
            pools: list[dict] = data.get("data", [])
            has_dsr = any(
                (p.get("project") or "").lower() == _DSR_PROJECT
                and (p.get("chain") or "").upper() == "ETHEREUM"
                for p in pools
            )
            return {
                "status": "ok" if has_dsr else "degraded",
                "last_success": self._last_success,
                "error": None,
            }
        except Exception as exc:
            return {
                "status": "down",
                "last_success": self._last_success,
                "error": str(exc),
            }
