"""
Katana adapter — yield-strategy vaults.

Architecture
────────────
Katana is a structured yield protocol that wraps underlying assets into
fixed or boosted-yield vaults (similar to a lightweight Pendle / Ribbon).
It has deployments across multiple chains.

No public documented REST or GraphQL API is currently available for Katana.
This adapter uses DeFiLlama as the data source, filtering pools where
``project`` matches the Katana DeFiLlama slug.

Opportunity modelling
─────────────────────
All Katana pools are modelled as ``VAULT`` opportunities on the SUPPLY side.
They are not AMM LP positions (LP detection is applied and LP pools skipped).

DeFiLlama APY encoding: already in percent (5.3 = 5.3%) — NO multiplication.

Chain support
─────────────
Determined dynamically from DeFiLlama response.  The adapter maps DeFiLlama
chain names to our Chain enum; unrecognised chains are skipped.

TODO (when official API is available)
───────────────────────────────────────
  Replace DeFiLlama with Katana's own API to gain:
  - Vault strategies and underlying composition
  - More granular APY breakdown (base vs. boosted)
  - Accurate TVL and cap data
  See: https://docs.katana.finance (if/when published)
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
    RewardBreakdown,
    RewardType,
)

log = structlog.get_logger(__name__)

# DeFiLlama project slugs that map to Katana.
# Multiple slugs because DeFiLlama sometimes uses "katana" vs "katana-finance".
_KATANA_PROJECTS: frozenset[str] = frozenset({"katana", "katana-finance", "katana-vaults"})

# Minimum pool TVL to avoid dust markets
_MIN_TVL_USD = 50_000.0

# DeFiLlama chain name → our Chain enum
_CHAIN_MAP: dict[str, Chain] = {
    "ETHEREUM": Chain.ETHEREUM,
    "ARBITRUM": Chain.ARBITRUM,
    "OPTIMISM": Chain.OPTIMISM,
    "BASE": Chain.BASE,
    "POLYGON": Chain.POLYGON,
    "BSC": Chain.BSC,
    "AVALANCHE": Chain.AVALANCHE,
    "SOLANA": Chain.SOLANA,
    "SEI": Chain.SEI,
}

_SOURCE_URL = "https://app.katana.finance"


class KatanaAdapter(ProtocolAdapter):
    """Katana yield-vault adapter — DeFiLlama-sourced, multi-chain."""

    @property
    def venue(self) -> Venue:
        return Venue.KATANA

    @property
    def protocol_name(self) -> str:
        return "Katana"

    @property
    def protocol_slug(self) -> str:
        return "katana"

    @property
    def supported_chains(self) -> list[Chain]:
        # Declared at a broad level; actual presence determined by DeFiLlama.
        return [
            Chain.ETHEREUM,
            Chain.ARBITRUM,
            Chain.OPTIMISM,
            Chain.BASE,
            Chain.SEI,
        ]

    @property
    def refresh_interval_seconds(self) -> int:
        return 600

    @property
    def requires_api_key(self) -> bool:
        return False

    @property
    def api_key_env_var(self) -> str | None:
        return None

    # -- Fetch -----------------------------------------------------------------

    async def fetch_opportunities(
        self,
        symbols: list[str] | None = None,
        chains: list[Chain] | None = None,
    ) -> list[MarketOpportunity]:
        effective_chains = set(chains or self.supported_chains)

        data = await get_json(settings.defillama_yields_url)
        pools: list[dict] = data.get("data", [])

        results: list[MarketOpportunity] = []
        for pool in pools:
            project = (pool.get("project") or "").lower()
            if project not in _KATANA_PROJECTS:
                continue

            chain_str = (pool.get("chain") or "").upper()
            chain = _CHAIN_MAP.get(chain_str)
            if chain is None or chain not in effective_chains:
                continue

            symbol_raw = pool.get("symbol", "")
            if not symbol_raw:
                continue

            if self.detect_and_skip_amm_lp(symbol_raw):
                log.debug("katana_skip_lp", symbol=symbol_raw)
                continue

            tvl_usd = _safe_float(pool.get("tvlUsd")) or 0.0
            if tvl_usd < _MIN_TVL_USD:
                continue

            canonical = self.normalize_symbol(symbol_raw, chain=chain)
            if symbols and canonical not in symbols:
                continue

            apy_pct = _safe_float(pool.get("apy")) or 0.0
            apy_base = _safe_float(pool.get("apyBase")) or 0.0
            apy_reward = _safe_float(pool.get("apyReward")) or 0.0
            pool_id = pool.get("pool") or symbol_raw

            reward_breakdown: list[RewardBreakdown] = []
            if apy_base > 0:
                reward_breakdown.append(RewardBreakdown(
                    reward_type=RewardType.NATIVE_YIELD,
                    apy_pct=apy_base,
                    is_variable=True,
                    notes="Base vault yield",
                ))
            if apy_reward > 0:
                reward_breakdown.append(RewardBreakdown(
                    reward_type=RewardType.TOKEN_INCENTIVE,
                    apy_pct=apy_reward,
                    is_variable=True,
                    notes="Token rewards",
                ))
            if not reward_breakdown:
                reward_breakdown.append(RewardBreakdown(
                    reward_type=RewardType.NATIVE_YIELD,
                    apy_pct=apy_pct,
                    is_variable=True,
                    notes="Vault yield",
                ))

            try:
                results.append(self.build_opportunity(
                    asset_id=canonical,
                    asset_symbol=symbol_raw,
                    chain=chain.value,
                    market_id=pool_id,
                    market_name=f"Katana {symbol_raw}",
                    side=OpportunitySide.SUPPLY,
                    opportunity_type=OpportunityType.VAULT,
                    effective_duration=EffectiveDuration.OVERNIGHT,
                    total_apy_pct=apy_pct,
                    base_apy_pct=apy_base if apy_base > 0 else apy_pct,
                    reward_breakdown=reward_breakdown,
                    tvl_usd=tvl_usd,
                    total_supplied_usd=tvl_usd,
                    liquidity=LiquidityInfo(available_liquidity_usd=tvl_usd),
                    is_collateral_eligible=False,
                    source_url=_SOURCE_URL,
                    tags=["katana", "vault"],
                ))
            except Exception as exc:
                log.warning("katana_pool_error", pool=pool_id, error=str(exc))

        log.info("katana_fetch_done", total=len(results))
        return results

    # -- Health check ----------------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        try:
            data = await get_json(settings.defillama_yields_url)
            pools = data.get("data", [])
            katana_pools = [
                p for p in pools
                if (p.get("project") or "").lower() in _KATANA_PROJECTS
            ]
            ok = len(katana_pools) > 0
            return {
                "status": "ok" if ok else "degraded",
                "last_success": self._last_success,
                "error": None,
            }
        except Exception as exc:
            return {"status": "down", "last_success": self._last_success, "error": str(exc)}


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
