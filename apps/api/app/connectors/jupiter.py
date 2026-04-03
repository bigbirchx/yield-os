"""
Jupiter adapter — Solana.

Architecture
────────────
Jupiter is a Solana DeFi aggregator.  For yield purposes, Yield OS tracks:

**Jupiter Lend** — an on-chain lending protocol (supply/borrow).
  Supports: USDC, WSOL, USDT, USDS, LBTC, cbBTC, PYUSD, EURC, JUPUSD, JLP,
  and various LSTs.  DeFiLlama only exposes the *supply* side; no borrow APY
  is available from the public API, so only SUPPLY opportunities are created.

**JLP (Jupiter Liquidity Pool)** — the perpetuals DEX liquidity pool.
  Liquidity providers deposit assets and receive JLP tokens that earn a share
  of trading fees from Jupiter's perpetual exchange.  Tagged as VAULT type
  (not AMM_LP — it is not a traditional AMM pair).

**JupSOL** — Jupiter's liquid-staked SOL token.
  Earns Solana native staking yield.  Modelled as SAVINGS type.

Data source
───────────
DeFiLlama yields API (``defillama_yields_url``):
  - ``project == "jupiter-lend"``      → lending + JLP vault
  - ``project == "jupiter-staked-sol"`` → JupSOL LST

APY encoding
────────────
DeFiLlama ``apy`` field is already in percentage format (4.5 = 4.5%).

Supported chains: SOLANA only.
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

# DeFiLlama project slugs
_LEND_PROJECT = "jupiter-lend"
_STAKED_SOL_PROJECT = "jupiter-staked-sol"

# JLP symbol identifier in DeFiLlama
_JLP_SYMBOL = "JLP"

# JupSOL symbol identifiers
_JUPSOL_SYMBOLS = {"JUPSOL", "JUPSOL-2"}


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


class JupiterAdapter(ProtocolAdapter):
    """Jupiter Lend + JLP + JupSOL adapter — Solana only, via DeFiLlama."""

    @property
    def venue(self) -> Venue:
        return Venue.JUPITER

    @property
    def protocol_name(self) -> str:
        return "Jupiter"

    @property
    def protocol_slug(self) -> str:
        return "jupiter"

    @property
    def supported_chains(self) -> list[Chain]:
        return [Chain.SOLANA]

    @property
    def refresh_interval_seconds(self) -> int:
        return 300

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
        if chains and Chain.SOLANA not in chains:
            return []

        data = await get_json(settings.defillama_yields_url)
        pools: list[dict] = data.get("data", [])

        all_opps: list[MarketOpportunity] = []
        for pool in pools:
            chain_str = (pool.get("chain") or "").upper()
            if chain_str != "SOLANA":
                continue

            project = pool.get("project", "")
            if project not in (_LEND_PROJECT, _STAKED_SOL_PROJECT):
                continue

            try:
                opps = self._parse_pool(pool)
                for opp in opps:
                    if symbols and opp.asset_id not in symbols:
                        continue
                    all_opps.append(opp)
            except Exception as exc:
                log.warning("jupiter_pool_error", pool=pool.get("pool", ""), error=str(exc))

        log.info("jupiter_fetch_done", total=len(all_opps))
        return all_opps

    # -- Pool parser -----------------------------------------------------------

    def _parse_pool(self, pool: dict) -> list[MarketOpportunity]:
        project = pool.get("project", "")
        symbol_raw = (pool.get("symbol") or "").strip()
        if not symbol_raw:
            return []

        apy_pct = _safe_float(pool.get("apy")) or 0.0
        tvl_usd = _safe_float(pool.get("tvlUsd"))
        pool_id = pool.get("pool", symbol_raw)

        # Skip tiny pools
        if tvl_usd is not None and tvl_usd < 1_000:
            return []

        sym_upper = symbol_raw.upper()

        # ── JupSOL (liquid staking) ──────────────────────────────────────────
        if project == _STAKED_SOL_PROJECT or sym_upper in _JUPSOL_SYMBOLS:
            return self._build_jupsol(symbol_raw, pool_id, apy_pct, tvl_usd)

        # ── JLP vault (perpetuals liquidity pool) ────────────────────────────
        if sym_upper == _JLP_SYMBOL:
            return self._build_jlp(pool_id, apy_pct, tvl_usd)

        # ── Jupiter Lend supply market ────────────────────────────────────────
        return self._build_lend_supply(symbol_raw, pool_id, apy_pct, tvl_usd)

    def _build_jupsol(
        self,
        symbol_raw: str,
        pool_id: str,
        apy_pct: float,
        tvl_usd: float | None,
    ) -> list[MarketOpportunity]:
        canonical = self.normalize_symbol(symbol_raw, chain=Chain.SOLANA)
        receipt = ReceiptTokenInfo(
            produces_receipt_token=True,
            receipt_token_symbol="JupSOL",
            is_transferable=True,
            is_composable=True,
            notes="Jupiter liquid-staked SOL — ERC-4626-style wrapper",
        )
        return [self.build_opportunity(
            asset_id=canonical,
            asset_symbol=symbol_raw,
            chain=Chain.SOLANA.value,
            market_id=f"jupiter:jupsol:{pool_id}",
            market_name="Jupiter Staked SOL (JupSOL)",
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
                    notes="Solana native staking yield via Jupiter validator",
                )
            ],
            tvl_usd=tvl_usd,
            total_supplied_usd=tvl_usd,
            liquidity=LiquidityInfo(available_liquidity_usd=tvl_usd),
            is_collateral_eligible=False,
            receipt_token=receipt,
            tags=["jupsol", "liquid-staking"],
            source_url="https://www.jup.ag/jup-staking",
        )]

    def _build_jlp(
        self,
        pool_id: str,
        apy_pct: float,
        tvl_usd: float | None,
    ) -> list[MarketOpportunity]:
        """JLP = Jupiter Perpetuals LP vault. VAULT type, not AMM_LP."""
        receipt = ReceiptTokenInfo(
            produces_receipt_token=True,
            receipt_token_symbol="JLP",
            is_transferable=True,
            is_composable=True,
            notes="Jupiter Liquidity Pool token — earns perp trading fees",
        )
        return [self.build_opportunity(
            asset_id="JLP",
            asset_symbol="JLP",
            chain=Chain.SOLANA.value,
            market_id=f"jupiter:jlp:{pool_id}",
            market_name="Jupiter Liquidity Pool (JLP)",
            side=OpportunitySide.SUPPLY,
            opportunity_type=OpportunityType.VAULT,
            effective_duration=EffectiveDuration.OVERNIGHT,
            total_apy_pct=apy_pct,
            base_apy_pct=apy_pct,
            reward_breakdown=[
                RewardBreakdown(
                    reward_type=RewardType.NATIVE_YIELD,
                    apy_pct=apy_pct,
                    is_variable=True,
                    notes="Perpetuals trading fee revenue shared with JLP holders",
                )
            ],
            tvl_usd=tvl_usd,
            total_supplied_usd=tvl_usd,
            liquidity=LiquidityInfo(available_liquidity_usd=tvl_usd),
            is_collateral_eligible=False,
            is_amm_lp=False,  # JLP is NOT an AMM pair — it is a perps liquidity vault
            receipt_token=receipt,
            tags=["jlp", "perpetuals"],
            source_url="https://jup.ag/perps/JLP",
        )]

    def _build_lend_supply(
        self,
        symbol_raw: str,
        pool_id: str,
        apy_pct: float,
        tvl_usd: float | None,
    ) -> list[MarketOpportunity]:
        """Jupiter Lend supply-side opportunity (DeFiLlama has no borrow APY)."""
        if self.detect_and_skip_amm_lp(symbol_raw):
            return []
        canonical = self.normalize_symbol(symbol_raw, chain=Chain.SOLANA)
        receipt = ReceiptTokenInfo(
            produces_receipt_token=True,
            receipt_token_symbol=f"j{symbol_raw}",
            is_transferable=True,
            is_composable=True,
            notes="Jupiter Lend supply position",
        )
        return [self.build_opportunity(
            asset_id=canonical,
            asset_symbol=symbol_raw,
            chain=Chain.SOLANA.value,
            market_id=f"jupiter:lend:{pool_id}",
            market_name=f"Jupiter Lend {symbol_raw}",
            side=OpportunitySide.SUPPLY,
            opportunity_type=OpportunityType.LENDING,
            effective_duration=EffectiveDuration.VARIABLE,
            total_apy_pct=apy_pct,
            base_apy_pct=apy_pct,
            reward_breakdown=[
                RewardBreakdown(
                    reward_type=RewardType.NATIVE_YIELD,
                    apy_pct=apy_pct,
                    is_variable=True,
                    notes="Variable supply APY",
                )
            ],
            tvl_usd=tvl_usd,
            total_supplied_usd=tvl_usd,
            liquidity=LiquidityInfo(available_liquidity_usd=tvl_usd),
            is_collateral_eligible=False,
            receipt_token=receipt,
            source_url="https://jup.ag/lend",
        )]

    # -- Health check ----------------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        try:
            data = await get_json(settings.defillama_yields_url)
            pools = data.get("data", [])
            jup_pools = [
                p for p in pools
                if p.get("project") in (_LEND_PROJECT, _STAKED_SOL_PROJECT)
                and (p.get("chain") or "").upper() == "SOLANA"
            ]
            ok = len(jup_pools) > 0
            return {"status": "ok" if ok else "degraded", "last_success": self._last_success, "error": None}
        except Exception as exc:
            return {"status": "down", "last_success": self._last_success, "error": str(exc)}
