"""
JustLend adapter — Tron lending protocol.

Architecture
────────────
JustLend is the dominant lending protocol on the Tron blockchain. It mirrors
the Compound V2 model: isolated markets per asset, cToken-style receipt tokens
(jTokens), fixed or variable supply/borrow APYs, and per-asset collateral
factors.

Key assets on JustLend:
  USDT  — by far the largest market (TRC-20 USDT, ~$1–2B TVL)
  USDC  — TRC-20 USDC
  TRX   — native Tron gas token
  JST   — JustLend governance / utility token
  SUN   — Sun.io ecosystem token
  BTT   — BitTorrent Token
  wBTC  — TRC-20 wrapped BTC
  wETH  — TRC-20 wrapped ETH

Data sources
────────────
Primary  : JustLend public REST API — no authentication required.
           GET  {justlend_api_url}/justlend/market_info_list
           Returns all markets with supply/borrow APY, TVL, collateral factor.

Fallback : DeFiLlama yields API — project="justlend", chain="Tron".
           Used when the official API is unreachable.
           Provides supply APY + TVL only (no borrow rates or collateral data).

APY encoding
────────────
Official API: decimal fractions (0.053 = 5.3%) — multiply ×100.
DeFiLlama:   already in percent (5.3 = 5.3%) — use as-is.

Supported chains: TRON only.
Refresh interval: 600 s.
"""
from __future__ import annotations

from typing import Any

import structlog

from app.connectors.base_adapter import ProtocolAdapter
from app.connectors.http_client import get_json
from app.core.config import settings
from asset_registry import Chain, Venue
from opportunity_schema import (
    CollateralAssetInfo,
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

_DEFILLAMA_URL = settings.defillama_yields_url
_DEFILLAMA_PROJECT = "justlend"

# Minimum market TVL to include (skip dust / test markets)
_MIN_TVL_USD = 100_000.0

_SOURCE_URL = "https://app.justlend.org/#/market"


class JustLendAdapter(ProtocolAdapter):
    """JustLend Finance adapter — Tron blockchain."""

    @property
    def venue(self) -> Venue:
        return Venue.JUSTLEND

    @property
    def protocol_name(self) -> str:
        return "JustLend"

    @property
    def protocol_slug(self) -> str:
        return "justlend"

    @property
    def supported_chains(self) -> list[Chain]:
        return [Chain.TRON]

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
        if chains and Chain.TRON not in chains:
            return []

        # Try the official JustLend API first; fall back to DeFiLlama on failure.
        try:
            opps = await self._fetch_from_official_api(symbols)
            log.info("justlend_fetch_done", source="official", total=len(opps))
            return opps
        except Exception as exc:
            log.warning("justlend_official_api_failed", error=str(exc), fallback="defillama")

        opps = await self._fetch_from_defillama(symbols)
        log.info("justlend_fetch_done", source="defillama", total=len(opps))
        return opps

    # -- Official JustLend API -------------------------------------------------

    async def _fetch_from_official_api(
        self, symbols: list[str] | None
    ) -> list[MarketOpportunity]:
        """
        Fetch all markets from the official JustLend REST API.

        Endpoint: GET /justlend/market_info_list
        Response structure:
          {
            "code": 0,
            "data": [
              {
                "tokenSymbol": "USDT",
                "supplyApy": 0.0453,      # decimal fraction
                "borrowApy": 0.0672,      # decimal fraction
                "totalSupplyUsd": 1.5e9,
                "totalBorrowUsd": 8.0e8,
                "collateralFactor": 0.85, # decimal fraction (LTV)
                "liquidationDiscount": 0.12,
                "price": 1.0,
                "jTokenAddress": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
              },
              ...
            ]
          }
        """
        url = f"{settings.justlend_api_url}/justlend/market_info_list"
        response = await get_json(url)
        if response.get("code", -1) != 0:
            raise ValueError(
                f"JustLend API error: code={response.get('code')}, "
                f"msg={response.get('msg', 'unknown')}"
            )
        markets: list[dict] = response.get("data") or []

        # Build collateral map for BORROW side (all markets with collateralFactor > 0)
        collateral_map = self._build_collateral_map(markets)

        results: list[MarketOpportunity] = []
        for market in markets:
            try:
                opps = self._build_official_opps(market, symbols, collateral_map)
                results.extend(opps)
            except Exception as exc:
                log.warning(
                    "justlend_market_error",
                    symbol=market.get("tokenSymbol", ""),
                    error=str(exc),
                )

        return results

    def _build_collateral_map(self, markets: list[dict]) -> dict[str, CollateralAssetInfo]:
        collateral_map: dict[str, CollateralAssetInfo] = {}
        for market in markets:
            cf = market.get("collateralFactor")
            if not cf:
                continue
            cf_pct = float(cf) * 100.0
            if cf_pct <= 0:
                continue
            symbol_raw = market.get("tokenSymbol", "")
            if not symbol_raw:
                continue
            canonical = self.normalize_symbol(symbol_raw, chain=Chain.TRON)
            supply_usd = _safe_float(market.get("totalSupplyUsd"))
            liq_discount = market.get("liquidationDiscount")
            liq_ltv_pct = (1.0 - float(liq_discount)) * 100.0 if liq_discount else cf_pct * 1.1
            collateral_map[canonical] = CollateralAssetInfo(
                asset_id=canonical,
                max_ltv_pct=cf_pct,
                liquidation_ltv_pct=min(liq_ltv_pct, 100.0),
                current_deposits=supply_usd,
            )
        return collateral_map

    def _build_official_opps(
        self,
        market: dict,
        symbols: list[str] | None,
        collateral_map: dict[str, CollateralAssetInfo],
    ) -> list[MarketOpportunity]:
        symbol_raw = market.get("tokenSymbol", "")
        if not symbol_raw:
            return []

        canonical = self.normalize_symbol(symbol_raw, chain=Chain.TRON)
        if symbols and canonical not in symbols:
            return []

        supply_usd = _safe_float(market.get("totalSupplyUsd")) or 0.0
        borrow_usd = _safe_float(market.get("totalBorrowUsd")) or 0.0

        if supply_usd < _MIN_TVL_USD:
            return []

        # APY: decimal fraction → percent
        supply_apy_pct = (_safe_float(market.get("supplyApy")) or 0.0) * 100.0
        borrow_apy_pct = (_safe_float(market.get("borrowApy")) or 0.0) * 100.0
        cf_raw = _safe_float(market.get("collateralFactor"))
        cf_pct = (cf_raw * 100.0) if cf_raw else None
        liq_discount = _safe_float(market.get("liquidationDiscount"))
        liq_ltv_pct = ((1.0 - liq_discount) * 100.0) if liq_discount else None

        avail_usd = max(supply_usd - borrow_usd, 0.0) if supply_usd > 0 else None
        util_pct = min(borrow_usd / supply_usd * 100.0, 100.0) if supply_usd > 0 else None

        liquidity = LiquidityInfo(
            available_liquidity_usd=avail_usd,
            utilization_rate_pct=util_pct,
        )

        j_token = f"j{symbol_raw.upper()}"
        receipt = ReceiptTokenInfo(
            produces_receipt_token=True,
            receipt_token_symbol=j_token,
            is_transferable=True,
            is_composable=True,
            notes=f"JustLend jToken (Compound V2-style interest-bearing token)",
        )

        market_id = market.get("jTokenAddress") or symbol_raw
        market_name = f"JustLend {symbol_raw}"

        results: list[MarketOpportunity] = []

        # SUPPLY
        results.append(self.build_opportunity(
            asset_id=canonical,
            asset_symbol=symbol_raw,
            chain=Chain.TRON.value,
            market_id=market_id,
            market_name=market_name,
            side=OpportunitySide.SUPPLY,
            opportunity_type=OpportunityType.LENDING,
            effective_duration=EffectiveDuration.VARIABLE,
            total_apy_pct=supply_apy_pct,
            base_apy_pct=supply_apy_pct,
            reward_breakdown=[
                RewardBreakdown(
                    reward_type=RewardType.NATIVE_YIELD,
                    apy_pct=supply_apy_pct,
                    is_variable=True,
                    notes="Variable supply APY",
                )
            ],
            tvl_usd=supply_usd,
            total_supplied_usd=supply_usd,
            liquidity=liquidity,
            is_collateral_eligible=bool(cf_pct and cf_pct > 0),
            as_collateral_max_ltv_pct=cf_pct,
            as_collateral_liquidation_ltv_pct=liq_ltv_pct,
            receipt_token=receipt,
            source_url=_SOURCE_URL,
            tags=["justlend", "tron"],
        ))

        # BORROW (only when collateral assets exist in the protocol)
        collateral_options = [
            c for c in collateral_map.values() if c.asset_id != canonical
        ]
        if collateral_options and borrow_apy_pct > 0:
            results.append(self.build_opportunity(
                asset_id=canonical,
                asset_symbol=symbol_raw,
                chain=Chain.TRON.value,
                market_id=f"{market_id}:borrow",
                market_name=market_name,
                side=OpportunitySide.BORROW,
                opportunity_type=OpportunityType.LENDING,
                effective_duration=EffectiveDuration.VARIABLE,
                total_apy_pct=borrow_apy_pct,
                base_apy_pct=borrow_apy_pct,
                reward_breakdown=[
                    RewardBreakdown(
                        reward_type=RewardType.NATIVE_YIELD,
                        apy_pct=borrow_apy_pct,
                        is_variable=True,
                        notes="Variable borrow APY (cost)",
                    )
                ],
                total_borrowed_usd=borrow_usd,
                liquidity=liquidity,
                collateral_options=collateral_options,
                source_url=_SOURCE_URL,
                tags=["justlend", "tron"],
            ))

        return results

    # -- DeFiLlama fallback ----------------------------------------------------

    async def _fetch_from_defillama(
        self, symbols: list[str] | None
    ) -> list[MarketOpportunity]:
        """Supply-only fallback via DeFiLlama yields pools (no borrow data)."""
        data = await get_json(_DEFILLAMA_URL)
        pools: list[dict] = data.get("data", [])

        results: list[MarketOpportunity] = []
        for pool in pools:
            project = (pool.get("project") or "").lower()
            if project != _DEFILLAMA_PROJECT:
                continue

            chain_str = (pool.get("chain") or "").upper()
            if chain_str != "TRON":
                continue

            symbol_raw = pool.get("symbol", "")
            tvl_usd = _safe_float(pool.get("tvlUsd")) or 0.0
            if tvl_usd < _MIN_TVL_USD:
                continue

            canonical = self.normalize_symbol(symbol_raw, chain=Chain.TRON)
            if symbols and canonical not in symbols:
                continue

            apy_pct = _safe_float(pool.get("apy")) or 0.0
            pool_id = pool.get("pool") or symbol_raw

            results.append(self.build_opportunity(
                asset_id=canonical,
                asset_symbol=symbol_raw,
                chain=Chain.TRON.value,
                market_id=pool_id,
                market_name=f"JustLend {symbol_raw}",
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
                        notes="Supply APY (DeFiLlama fallback — borrow data unavailable)",
                    )
                ],
                tvl_usd=tvl_usd,
                total_supplied_usd=tvl_usd,
                liquidity=LiquidityInfo(available_liquidity_usd=tvl_usd),
                is_collateral_eligible=False,
                source_url=_SOURCE_URL,
                tags=["justlend", "tron", "defillama-fallback"],
            ))

        return results

    # -- Health check ----------------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        try:
            url = f"{settings.justlend_api_url}/justlend/market_info_list"
            response = await get_json(url)
            ok = response.get("code") == 0 and bool(response.get("data"))
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
