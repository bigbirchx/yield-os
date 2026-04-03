"""
SparkLend + Sky Savings adapter.

Architecture
────────────
SparkLend is a fork of Aave V3 on Ethereum maintained by Sky (formerly MakerDAO).
The protocol uses the same Messari-compatible subgraph schema as Aave V3.

In addition to the standard lending markets, this adapter exposes two native
savings-rate products from the Sky/Maker ecosystem:

  sDAI  — DAI Savings Rate (DSR) wrapped via ERC-4626 Savings DAI
  sUSDS — Sky Savings Rate (SSR) wrapped via ERC-4626 Savings USDS

Both are fetched from the DeFiLlama yields API (``sky_savings_url`` setting)
and modelled as ``SAVINGS`` type opportunities with ``OVERNIGHT`` duration.

Data sources
────────────
- SparkLend reserves  : Messari subgraph (``spark_url``)
- DSR / SSR rates     : DeFiLlama yields pools (``sky_savings_url``)

APY encoding
────────────
- Messari subgraph     : percentages (5.0 = 5%)     — NO multiplication needed
- DeFiLlama ``apy``   : percentages (5.0 = 5%)     — NO multiplication needed

Supported chains: ETHEREUM only (SparkLend mainnet + Maker savings).
"""
from __future__ import annotations

import asyncio
from typing import Any

import structlog

from app.connectors.base_adapter import ProtocolAdapter
from app.connectors.http_client import get_json, post_json
from app.core.config import settings
from asset_registry import Chain, Venue
from opportunity_schema import (
    CollateralAssetInfo,
    EffectiveDuration,
    LiquidityInfo,
    MarketOpportunity,
    OpportunitySide,
    OpportunityType,
    RateModelInfo,
    ReceiptTokenInfo,
    RewardBreakdown,
    RewardType,
)

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# DeFiLlama pool identifiers for Sky savings products
# Pool IDs are stable; they are the DeFiLlama internal pool slugs.
# ---------------------------------------------------------------------------

# DeFiLlama project slugs that identify DSR and SSR pools
_DSR_PROJECT = "maker-dsr"
_SSR_PROJECT = "sky"

# Fallback pool-name substrings if project slug matching fails
_DSR_POOL_NAME_HINT = "DAI"
_SSR_POOL_NAME_HINT = "USDS"

# ---------------------------------------------------------------------------
# GraphQL query — Messari-compatible Aave V3 fork schema
# ---------------------------------------------------------------------------

_RESERVES_QUERY = """
{
  markets(
    orderBy: totalValueLockedUSD
    orderDirection: desc
    first: 100
  ) {
    id
    name
    inputToken {
      id
      symbol
      decimals
    }
    outputToken {
      id
      symbol
    }
    rates {
      rate
      side
      type
    }
    totalValueLockedUSD
    totalDepositBalanceUSD
    totalBorrowBalanceUSD
    maximumLTV
    liquidationThreshold
    liquidationPenalty
    supplyCap
    borrowCap
    canBorrowFrom
    canUseAsCollateral
    isActive
    protocol {
      id
      name
    }
  }
}
"""

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


def _extract_rate(rates: list[dict], side: str, rate_type: str = "VARIABLE") -> float | None:
    for r in rates:
        if r.get("side") == side and r.get("type") == rate_type:
            try:
                return float(r["rate"])
            except (KeyError, ValueError, TypeError):
                pass
    return None


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class SparkAdapter(ProtocolAdapter):
    """SparkLend + Sky Savings adapter — Ethereum only."""

    @property
    def venue(self) -> Venue:
        return Venue.SPARK

    @property
    def protocol_name(self) -> str:
        return "SparkLend"

    @property
    def protocol_slug(self) -> str:
        return "sparklend"

    @property
    def supported_chains(self) -> list[Chain]:
        return [Chain.ETHEREUM]

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
        if chains and Chain.ETHEREUM not in chains:
            return []

        spark_task = self._fetch_spark_markets(symbols)
        savings_task = self._fetch_sky_savings(symbols)
        spark_opps, savings_opps = await asyncio.gather(spark_task, savings_task, return_exceptions=True)

        all_opps: list[MarketOpportunity] = []
        if isinstance(spark_opps, Exception):
            log.warning("spark_markets_error", error=str(spark_opps))
        else:
            all_opps.extend(spark_opps)

        if isinstance(savings_opps, Exception):
            log.warning("sky_savings_error", error=str(savings_opps))
        else:
            all_opps.extend(savings_opps)

        log.info("spark_fetch_done", total=len(all_opps))
        return all_opps

    # -- SparkLend lending markets ---------------------------------------------

    async def _fetch_spark_markets(self, symbols: list[str] | None) -> list[MarketOpportunity]:
        body = await post_json(settings.spark_url, data={"query": _RESERVES_QUERY})
        if "errors" in body:
            raise ValueError(f"SparkLend subgraph error: {body['errors']}")
        markets = body.get("data", {}).get("markets", [])

        all_reserves = [m for m in markets if m.get("canBorrowFrom") or m.get("canUseAsCollateral")]

        # Build a collateral map: asset_id → CollateralAssetInfo (for BORROW side)
        collateral_map = self._build_collateral_map(all_reserves)

        results: list[MarketOpportunity] = []
        for market in all_reserves:
            if not market.get("canBorrowFrom"):
                continue
            if not market.get("isActive", True):
                continue

            token = market.get("inputToken") or {}
            symbol_raw = token.get("symbol", "")
            if not symbol_raw or self.detect_and_skip_amm_lp(symbol_raw):
                continue

            canonical = self.normalize_symbol(symbol_raw, chain=Chain.ETHEREUM)
            if symbols and canonical not in symbols:
                continue

            rates = market.get("rates") or []
            supply_apy = _extract_rate(rates, "LENDER", "VARIABLE") or 0.0
            borrow_apy = _extract_rate(rates, "BORROWER", "VARIABLE") or 0.0
            supply_reward_apy = _extract_rate(rates, "LENDER", "REWARD") or 0.0
            borrow_reward_apy = _extract_rate(rates, "BORROWER", "REWARD") or 0.0
            supply_total_apy = supply_apy + supply_reward_apy
            borrow_net_apy = max(borrow_apy - borrow_reward_apy, 0.0)

            supply_usd = _safe_float(market.get("totalDepositBalanceUSD")) or _safe_float(market.get("totalValueLockedUSD"))
            borrow_usd = _safe_float(market.get("totalBorrowBalanceUSD"))

            avail_usd = None
            if supply_usd is not None and borrow_usd is not None:
                avail_usd = max(supply_usd - borrow_usd, 0.0)
            util_pct = None
            if supply_usd and borrow_usd and supply_usd > 0:
                util_pct = min(borrow_usd / supply_usd * 100.0, 100.0)

            liquidity = LiquidityInfo(
                available_liquidity_usd=avail_usd,
                utilization_rate_pct=util_pct,
            )

            rate_model = RateModelInfo(
                model_type="sparklend-variable",
                current_supply_rate_pct=supply_apy,
                current_borrow_rate_pct=borrow_apy,
            )

            output_token = market.get("outputToken") or {}
            receipt = ReceiptTokenInfo(
                produces_receipt_token=True,
                receipt_token_symbol=output_token.get("symbol") or f"sp{symbol_raw}",
                is_transferable=True,
                is_composable=True,
                notes="SparkLend spToken (interest-bearing, Aave V3 fork)",
            )

            max_ltv = _safe_float(market.get("maximumLTV"))
            liq_ltv = _safe_float(market.get("liquidationThreshold"))
            market_id = market.get("id", symbol_raw)
            market_name = f"SparkLend {symbol_raw}"
            source_url = "https://app.spark.fi/markets"

            supply_rewards: list[RewardBreakdown] = [
                RewardBreakdown(
                    reward_type=RewardType.NATIVE_YIELD,
                    apy_pct=supply_apy,
                    is_variable=True,
                    notes="Variable supply APY",
                ),
            ]
            if supply_reward_apy > 0:
                supply_rewards.append(RewardBreakdown(
                    reward_type=RewardType.TOKEN_INCENTIVE,
                    apy_pct=supply_reward_apy,
                    is_variable=True,
                    notes="SPK/SKY token rewards",
                ))

            # SUPPLY
            results.append(self.build_opportunity(
                asset_id=canonical,
                asset_symbol=symbol_raw,
                chain=Chain.ETHEREUM.value,
                market_id=market_id,
                market_name=market_name,
                side=OpportunitySide.SUPPLY,
                opportunity_type=OpportunityType.LENDING,
                effective_duration=EffectiveDuration.VARIABLE,
                total_apy_pct=supply_total_apy,
                base_apy_pct=supply_apy,
                reward_breakdown=supply_rewards,
                total_supplied_usd=supply_usd,
                tvl_usd=supply_usd,
                liquidity=liquidity,
                rate_model=rate_model,
                is_collateral_eligible=bool(max_ltv and max_ltv > 0),
                as_collateral_max_ltv_pct=max_ltv,
                as_collateral_liquidation_ltv_pct=liq_ltv,
                receipt_token=receipt,
                source_url=source_url,
            ))

            # BORROW
            collateral_options = list(collateral_map.values())
            # Remove self-referencing collateral (borrow asset as its own collateral)
            collateral_options = [c for c in collateral_options if c.asset_id != canonical]

            borrow_rewards: list[RewardBreakdown] = [
                RewardBreakdown(
                    reward_type=RewardType.NATIVE_YIELD,
                    apy_pct=borrow_apy,
                    is_variable=True,
                    notes="Variable borrow APY (cost)",
                ),
            ]
            if borrow_reward_apy > 0:
                borrow_rewards.append(RewardBreakdown(
                    reward_type=RewardType.TOKEN_INCENTIVE,
                    apy_pct=borrow_reward_apy,
                    is_variable=True,
                    notes="SPK/SKY rewards offset borrow cost",
                ))

            if collateral_options:
                results.append(self.build_opportunity(
                    asset_id=canonical,
                    asset_symbol=symbol_raw,
                    chain=Chain.ETHEREUM.value,
                    market_id=f"{market_id}:borrow",
                    market_name=market_name,
                    side=OpportunitySide.BORROW,
                    opportunity_type=OpportunityType.LENDING,
                    effective_duration=EffectiveDuration.VARIABLE,
                    total_apy_pct=borrow_net_apy,
                    base_apy_pct=borrow_apy,
                    reward_breakdown=borrow_rewards,
                    total_borrowed_usd=borrow_usd,
                    liquidity=liquidity,
                    rate_model=rate_model,
                    collateral_options=collateral_options,
                    source_url=source_url,
                ))

        return results

    def _build_collateral_map(self, markets: list[dict]) -> dict[str, CollateralAssetInfo]:
        collateral_map: dict[str, CollateralAssetInfo] = {}
        for market in markets:
            if not market.get("canUseAsCollateral"):
                continue
            token = market.get("inputToken") or {}
            symbol_raw = token.get("symbol", "")
            if not symbol_raw or self.detect_and_skip_amm_lp(symbol_raw):
                continue
            max_ltv = _safe_float(market.get("maximumLTV"))
            liq_ltv = _safe_float(market.get("liquidationThreshold"))
            if not max_ltv or max_ltv <= 0:
                continue
            canonical = self.normalize_symbol(symbol_raw, chain=Chain.ETHEREUM)
            tvl = _safe_float(market.get("totalValueLockedUSD"))
            collateral_map[canonical] = CollateralAssetInfo(
                asset_id=canonical,
                max_ltv_pct=max_ltv,
                liquidation_ltv_pct=liq_ltv or max_ltv,
                current_deposits=tvl,
            )
        return collateral_map

    # -- Sky savings rates (DSR + SSR) ----------------------------------------

    async def _fetch_sky_savings(self, symbols: list[str] | None) -> list[MarketOpportunity]:
        """Fetch sDAI DSR and sUSDS SSR from DeFiLlama yields API."""
        data = await get_json(settings.sky_savings_url)
        pools: list[dict] = data.get("data", [])

        results: list[MarketOpportunity] = []
        for pool in pools:
            project = (pool.get("project") or "").lower()
            symbol_raw = pool.get("symbol", "")
            chain_str = (pool.get("chain") or "").upper()

            if chain_str != "ETHEREUM":
                continue

            is_dsr = project == _DSR_PROJECT or (
                "dsr" in project and _DSR_POOL_NAME_HINT in symbol_raw.upper()
            )
            is_ssr = project == _SSR_PROJECT or (
                "sky" in project and _SSR_POOL_NAME_HINT in symbol_raw.upper()
            )

            if not (is_dsr or is_ssr):
                continue

            apy_pct = _safe_float(pool.get("apy"))
            if apy_pct is None:
                continue

            if is_dsr:
                canonical = "DAI"
                receipt_symbol = "sDAI"
                market_id = "spark:dsr:sdai"
                market_name = "Sky DSR (sDAI)"
                source_url = "https://app.spark.fi/savings"
                notes = "DAI Savings Rate via Spark/MakerDAO DSR"
            else:
                canonical = "USDS"
                receipt_symbol = "sUSDS"
                market_id = "spark:ssr:susds"
                market_name = "Sky Savings Rate (sUSDS)"
                source_url = "https://app.spark.fi/savings"
                notes = "USDS Sky Savings Rate (SSR)"

            if symbols and canonical not in symbols:
                continue

            tvl_usd = _safe_float(pool.get("tvlUsd"))

            receipt = ReceiptTokenInfo(
                produces_receipt_token=True,
                receipt_token_symbol=receipt_symbol,
                is_transferable=True,
                is_composable=True,
                notes=f"ERC-4626 savings wrapper — {notes}",
            )

            results.append(self.build_opportunity(
                asset_id=canonical,
                asset_symbol=canonical,
                chain=Chain.ETHEREUM.value,
                market_id=market_id,
                market_name=market_name,
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
                        notes=notes,
                    )
                ],
                tvl_usd=tvl_usd,
                total_supplied_usd=tvl_usd,
                liquidity=LiquidityInfo(available_liquidity_usd=tvl_usd),
                is_collateral_eligible=False,
                receipt_token=receipt,
                source_url=source_url,
            ))

        return results

    async def health_check(self) -> dict[str, Any]:
        try:
            body = await post_json(settings.spark_url, data={"query": "{ markets(first: 1) { id } }"})
            ok = bool(body.get("data", {}).get("markets"))
            return {"status": "ok" if ok else "degraded", "last_success": self._last_success, "error": None}
        except Exception as exc:
            return {"status": "down", "last_success": self._last_success, "error": str(exc)}
