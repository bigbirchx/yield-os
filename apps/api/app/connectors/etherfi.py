"""
EtherFi adapter — liquid restaking and liquid vaults on Ethereum.

Products
────────
eETH — EtherFi native liquid restaking token.
       Users deposit ETH; EtherFi deploys Ethereum validators and registers
       them on EigenLayer AVSes to earn restaking rewards on top of base
       staking yield.  eETH is a rebasing token (balance increases daily).

weETH — Wrapped eETH (ERC-4626, non-rebasing).
        Same yield as eETH; preferred as DeFi collateral.
        Used in Aave V3, Morpho Blue, Compound V3, and SparkLend.
        Not a separate deposit — wrapping eETH is a zero-cost 1:1 conversion.

EtherFi Liquid — curated EigenLayer strategy vaults.
        Multi-protocol vaults that allocate deposits across EigenLayer AVSes
        and DeFi protocols to generate blended yield.  Each vault accepts a
        specific asset (e.g. ETH, USDC, WBTC).

Reward structure
────────────────
  NATIVE_YIELD  Base ETH staking APY from Ethereum validator rewards (~3–4 %).
  POINTS        EtherFi loyalty points (off-chain; no fixed APY).
  POINTS        EigenLayer restaking points (off-chain; no fixed APY).

Data sources
────────────
Primary: DeFiLlama yields API (``defillama_yields_url`` setting).
  - project == "ether.fi"          → eETH staking pool (symbol ETH/eETH)
  - project == "ether.fi-liquid"   → EtherFi Liquid vault pools

The DeFiLlama ``apy`` field rolls up base staking yield; points are additive
off-chain rewards and are captured as separate POINTS reward components with
``apy_pct=0.0``.

DeFiLlama project slugs for EtherFi
────────────────────────────────────
Staking  : "ether.fi"          (main eETH restaking product)
Liquid   : "ether.fi-liquid"   (EtherFi Liquid strategy vaults)
Both checked case-insensitively to tolerate slug renames.

APY encoding
────────────
DeFiLlama ``apy`` is already a percentage (3.5 = 3.5 %). No multiplication.

Supported chains: ETHEREUM.
Refresh interval: 900 s.
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
# DeFiLlama project slugs (case-insensitive matching used throughout)
# ---------------------------------------------------------------------------

_STAKING_PROJECT = "ether.fi"
_LIQUID_PROJECT = "ether.fi-liquid"

# Symbols that identify the eETH staking pool (underlying deposit is ETH)
_STAKING_SYMBOLS = {"ETH", "WETH", "EETH"}

# Minimum TVL threshold — skip ghost / test pools
_MIN_TVL_USD = 10_000.0

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


def _project_matches(project: str, target: str) -> bool:
    """Case-insensitive project-slug prefix match."""
    return project.lower().startswith(target.lower())


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class EtherFiAdapter(ProtocolAdapter):
    """EtherFi liquid restaking and liquid vault adapter — Ethereum only."""

    @property
    def venue(self) -> Venue:
        return Venue.ETHERFI

    @property
    def protocol_name(self) -> str:
        return "EtherFi"

    @property
    def protocol_slug(self) -> str:
        return "ether.fi"

    @property
    def supported_chains(self) -> list[Chain]:
        return [Chain.ETHEREUM]

    @property
    def refresh_interval_seconds(self) -> int:
        return 900

    @property
    def requires_api_key(self) -> bool:
        return False

    @property
    def api_key_env_var(self) -> str | None:
        return None

    def __init__(self) -> None:
        super().__init__()
        self._defillama_url = settings.defillama_yields_url

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

        opportunities: list[MarketOpportunity] = []
        seen_staking = False  # deduplicate — only one eETH staking opp

        for pool in pools:
            chain_str = (pool.get("chain") or "").upper()
            if chain_str != "ETHEREUM":
                continue

            project = (pool.get("project") or "").lower()
            if not (
                _project_matches(project, _STAKING_PROJECT)
                or _project_matches(project, _LIQUID_PROJECT)
            ):
                continue

            # Skip tiny / ghost pools
            tvl = _safe_float(pool.get("tvlUsd"))
            if tvl is not None and tvl < _MIN_TVL_USD:
                continue

            try:
                if _project_matches(project, _STAKING_PROJECT) and not seen_staking:
                    sym_upper = (pool.get("symbol") or "").upper()
                    if sym_upper in _STAKING_SYMBOLS:
                        opp = self._build_staking_opportunity(pool)
                        if opp is not None:
                            if not symbols or opp.asset_id in symbols:
                                opportunities.append(opp)
                            seen_staking = True
                elif _project_matches(project, _LIQUID_PROJECT):
                    opp = self._build_liquid_vault(pool)
                    if opp is not None:
                        if not symbols or opp.asset_id in symbols:
                            opportunities.append(opp)
            except Exception as exc:
                log.warning(
                    "etherfi_pool_parse_error",
                    pool=pool.get("pool", "?"),
                    project=project,
                    error=str(exc),
                )

        log.info("etherfi_fetch_done", opportunities=len(opportunities))
        return opportunities

    # -- eETH staking ----------------------------------------------------------

    def _build_staking_opportunity(self, pool: dict) -> MarketOpportunity | None:
        apy_pct = _safe_float(pool.get("apy")) or 0.0
        apy_base = _safe_float(pool.get("apyBase")) or apy_pct
        tvl_usd = _safe_float(pool.get("tvlUsd"))

        # Reward breakdown:
        # 1. Base staking yield from Ethereum validator rewards
        # 2. EtherFi loyalty points (off-chain, not expressed as APY)
        # 3. EigenLayer restaking points (off-chain, not expressed as APY)
        rewards: list[RewardBreakdown] = [
            RewardBreakdown(
                reward_type=RewardType.NATIVE_YIELD,
                apy_pct=apy_base,
                is_variable=True,
                notes="Ethereum validator staking yield (consensus + execution rewards)",
            ),
            RewardBreakdown(
                reward_type=RewardType.POINTS,
                token_name="EtherFi Points",
                apy_pct=0.0,
                is_variable=True,
                notes=(
                    "EtherFi loyalty points accumulate proportional to staked ETH × time. "
                    "Value depends on future EtherFi token distribution."
                ),
            ),
            RewardBreakdown(
                reward_type=RewardType.POINTS,
                token_name="EigenLayer Points",
                apy_pct=0.0,
                is_variable=True,
                notes=(
                    "EigenLayer restaking points earned via EtherFi's EigenPod delegation. "
                    "Value depends on EigenLayer EIGEN token distribution."
                ),
            ),
        ]

        # eETH is the rebasing receipt token.
        # weETH is the non-rebasing wrapper — users wrap eETH to use in DeFi.
        receipt = ReceiptTokenInfo(
            produces_receipt_token=True,
            receipt_token_id="eETH",
            receipt_token_symbol="eETH",
            is_transferable=True,
            is_composable=True,
            composable_venues=["Aave V3", "Morpho Blue", "Compound V3", "SparkLend"],
            notes=(
                "eETH is a rebasing LRT (balance increases daily with rewards). "
                "Wrap eETH → weETH (ERC-4626, non-rebasing) for use as DeFi collateral "
                "in Aave V3, Morpho Blue, Compound V3, SparkLend, and others. "
                "weETH wrapping is a zero-cost 1:1 conversion via ether.fi app."
            ),
        )

        return self.build_opportunity(
            asset_id="ETH",
            asset_symbol="ETH",
            chain=Chain.ETHEREUM.value,
            market_id="etherfi:eeth:ethereum",
            market_name="EtherFi eETH Liquid Restaking",
            side=OpportunitySide.SUPPLY,
            opportunity_type=OpportunityType.RESTAKING,
            effective_duration=EffectiveDuration.VARIABLE,
            total_apy_pct=apy_pct,
            base_apy_pct=apy_base,
            reward_breakdown=rewards,
            tvl_usd=tvl_usd,
            total_supplied_usd=tvl_usd,
            is_capacity_capped=False,
            liquidity=LiquidityInfo(
                has_lockup=False,
                available_liquidity_usd=tvl_usd,
                notes=(
                    "No protocol lock-up. Unstaking via EtherFi withdrawal queue "
                    "(hours–days) or secondary market swap (instant, market slippage)."
                ),
            ),
            is_collateral_eligible=False,
            receipt_token=receipt,
            tags=["liquid-restaking", "eigenlayer", "points", "etherfi"],
            source_url="https://app.ether.fi/defi",
        )

    # -- EtherFi Liquid vaults -------------------------------------------------

    def _build_liquid_vault(self, pool: dict) -> MarketOpportunity | None:
        symbol_raw = (pool.get("symbol") or "").strip()
        if not symbol_raw:
            return None
        if self.detect_and_skip_amm_lp(symbol_raw):
            return None

        canonical = self.normalize_symbol(symbol_raw, chain=Chain.ETHEREUM)

        apy_pct = _safe_float(pool.get("apy")) or 0.0
        tvl_usd = _safe_float(pool.get("tvlUsd"))
        pool_id = pool.get("pool") or f"etherfi-liquid:{symbol_raw}"
        vault_name = pool.get("poolMeta") or f"EtherFi Liquid {symbol_raw}"

        # Base yield from the vault strategy
        rewards: list[RewardBreakdown] = [
            RewardBreakdown(
                reward_type=RewardType.NATIVE_YIELD,
                apy_pct=apy_pct,
                is_variable=True,
                notes="Blended yield from EtherFi Liquid vault strategy (EigenLayer AVSes + DeFi)",
            ),
        ]

        # EigenLayer points apply broadly to EtherFi Liquid vaults
        rewards.append(
            RewardBreakdown(
                reward_type=RewardType.POINTS,
                token_name="EigenLayer Points",
                apy_pct=0.0,
                is_variable=True,
                notes="EigenLayer restaking points where vault allocates to EigenLayer AVSes",
            ),
        )

        return self.build_opportunity(
            asset_id=canonical,
            asset_symbol=symbol_raw,
            chain=Chain.ETHEREUM.value,
            market_id=pool_id,
            market_name=vault_name,
            side=OpportunitySide.SUPPLY,
            opportunity_type=OpportunityType.VAULT,
            effective_duration=EffectiveDuration.OVERNIGHT,
            total_apy_pct=apy_pct,
            base_apy_pct=apy_pct,
            reward_breakdown=rewards,
            tvl_usd=tvl_usd,
            total_supplied_usd=tvl_usd,
            liquidity=LiquidityInfo(available_liquidity_usd=tvl_usd),
            is_collateral_eligible=False,
            tags=["etherfi-liquid", "eigenlayer", "vault"],
            source_url="https://app.ether.fi/liquid",
        )

    # -- Health check ----------------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        try:
            data = await get_json(self._defillama_url)
            pools: list[dict] = data.get("data", [])
            etherfi_pools = [
                p for p in pools
                if _project_matches((p.get("project") or ""), _STAKING_PROJECT)
                and (p.get("chain") or "").upper() == "ETHEREUM"
            ]
            return {
                "status": "ok" if etherfi_pools else "degraded",
                "last_success": self._last_success,
                "error": None,
            }
        except Exception as exc:
            return {
                "status": "down",
                "last_success": self._last_success,
                "error": str(exc),
            }
