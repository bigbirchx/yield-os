"""
Kamino Finance adapter — Solana lending + liquidity vaults.

Architecture
────────────
Kamino has two distinct products:

**Kamino Lend** — isolated lending markets on Solana, similar to Aave V3.
  Each *market* contains multiple *reserves* (one per asset).
  - Any reserve can be supplied to earn variable interest  → SUPPLY opportunity
  - Any reserve can be borrowed against eligible collateral → BORROW opportunity
  - Each reserve has a ``maxLtv`` that determines how much it contributes as
    collateral.  For a BORROW on asset X, collateral_options = all *other*
    reserves in the same market that have maxLtv > 0.

  Special case — kToken reserves (e.g. ``kSOLJITOSOLOrca``):
    These are Kamino Liquidity kTokens deposited into the lending market as
    collateral.  They represent CLMM LP positions; tagged ``is_amm_lp=True``.

**Kamino Liquidity** — automated CLMM LP management vaults.
  kToken holders receive a share of the underlying LP fees.
  Data sourced from DeFiLlama (``defillama_yields_url``).
  All liquidity vaults are tagged ``is_amm_lp=True``.

Data sources
────────────
  Kamino Lend  : GET {kamino_api_url}/v2/kamino-market?env=mainnet-beta
                 GET {kamino_api_url}/kamino-market/{addr}/reserves/metrics
  Kamino Liquidity : GET {defillama_yields_url}  (project=kamino-liquidity)

APY encoding
────────────
  Kamino REST API  : decimal fractions (0.048 = 4.8%)  → multiply ×100
  DeFiLlama ``apy``: percentage  (4.8 = 4.8%)           → use as-is
  ``maxLtv``       : decimal string (``"0.8"`` = 80%)   → multiply ×100

Supported chains: SOLANA only.
"""
from __future__ import annotations

import asyncio
import re
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
    RateModelInfo,
    ReceiptTokenInfo,
    RewardBreakdown,
    RewardType,
)

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# kToken detection
# kTokens are Kamino Liquidity receipts used as collateral in Kamino Lend.
# Pattern: starts with "k" followed by two token names and a DEX suffix.
# Examples: kSOLJITOSOLOrca, kSOLMSOLRaydium, kUXDUSDCOrca
# ---------------------------------------------------------------------------

_KTOKEN_RE = re.compile(
    r"^k[A-Z][A-Za-z0-9]+(?:Orca|Raydium|Meteora|Whirlpool|Kamino)$"
)


def _is_ktoken(symbol: str) -> bool:
    return bool(_KTOKEN_RE.match(symbol))


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


def _ltv_pct(val: Any) -> float | None:
    """Convert a decimal LTV string/float to percentage. Returns None if ≤ 0."""
    f = _safe_float(val)
    if f is None or f <= 0:
        return None
    return f * 100.0


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class KaminoAdapter(ProtocolAdapter):
    """Kamino Finance adapter — Solana lending markets + liquidity vaults."""

    @property
    def venue(self) -> Venue:
        return Venue.KAMINO

    @property
    def protocol_name(self) -> str:
        return "Kamino"

    @property
    def protocol_slug(self) -> str:
        return "kamino"

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

        lend_task = self._fetch_lending_markets(symbols)
        liquidity_task = self._fetch_liquidity_vaults(symbols)
        lend_results, liquidity_results = await asyncio.gather(
            lend_task, liquidity_task, return_exceptions=True
        )

        all_opps: list[MarketOpportunity] = []
        if isinstance(lend_results, Exception):
            log.warning("kamino_lend_error", error=str(lend_results))
        else:
            all_opps.extend(lend_results)

        if isinstance(liquidity_results, Exception):
            log.warning("kamino_liquidity_error", error=str(liquidity_results))
        else:
            all_opps.extend(liquidity_results)

        log.info("kamino_fetch_done", total=len(all_opps))
        return all_opps

    # -- Lending markets -------------------------------------------------------

    async def _fetch_lending_markets(self, symbols: list[str] | None) -> list[MarketOpportunity]:
        markets_raw = await get_json(
            f"{settings.kamino_api_url}/v2/kamino-market",
            params={"env": "mainnet-beta"},
        )
        if not isinstance(markets_raw, list):
            markets_raw = markets_raw.get("markets", [])

        # Fetch all market reserves concurrently
        reserve_tasks = [
            self._fetch_reserves(m["lendingMarket"])
            for m in markets_raw
        ]
        reserve_results = await asyncio.gather(*reserve_tasks, return_exceptions=True)

        all_opps: list[MarketOpportunity] = []
        for market, reserves in zip(markets_raw, reserve_results):
            if isinstance(reserves, Exception):
                log.warning(
                    "kamino_reserves_error",
                    market=market.get("lendingMarket", ""),
                    error=str(reserves),
                )
                continue
            try:
                opps = self._build_market_opportunities(market, reserves)
                for opp in opps:
                    if symbols and opp.asset_id not in symbols:
                        continue
                    all_opps.append(opp)
            except Exception as exc:
                log.warning(
                    "kamino_market_build_error",
                    market=market.get("name", ""),
                    error=str(exc),
                )
        return all_opps

    async def _fetch_reserves(self, market_address: str) -> list[dict]:
        data = await get_json(
            f"{settings.kamino_api_url}/kamino-market/{market_address}/reserves/metrics"
        )
        if isinstance(data, list):
            return data
        return data.get("reserves", [])

    def _build_market_opportunities(
        self,
        market: dict,
        reserves: list[dict],
    ) -> list[MarketOpportunity]:
        market_address = market.get("lendingMarket", "")
        market_name = market.get("name") or f"Kamino {market_address[:8]}"

        # Build collateral map — all reserves with LTV > 0
        collateral_map: dict[str, CollateralAssetInfo] = {}
        for r in reserves:
            symbol_raw = r.get("liquidityToken", "")
            if not symbol_raw:
                continue
            ltv = _ltv_pct(r.get("maxLtv"))
            if ltv is None:
                continue
            canonical = self.normalize_symbol(symbol_raw, chain=Chain.SOLANA)
            tvl = _safe_float(r.get("totalSupplyUsd"))
            collateral_map[canonical] = CollateralAssetInfo(
                asset_id=canonical,
                max_ltv_pct=ltv,
                liquidation_ltv_pct=ltv,  # Kamino API does not expose liq threshold separately
                current_deposits=tvl,
            )

        results: list[MarketOpportunity] = []
        for r in reserves:
            symbol_raw = r.get("liquidityToken", "")
            if not symbol_raw:
                continue

            # Detect kTokens (Kamino Liquidity receipt tokens)
            is_ktoken = _is_ktoken(symbol_raw)
            if is_ktoken:
                # kTokens are AMM LP positions — skip AMM LP from standalone opps
                # if detect_and_skip_amm_lp would otherwise catch them, but we
                # still want to model them as supply opportunities in the lending market
                pass
            elif self.detect_and_skip_amm_lp(symbol_raw):
                continue

            canonical = self.normalize_symbol(symbol_raw, chain=Chain.SOLANA)
            supply_apy_raw = _safe_float(r.get("supplyApy"))
            borrow_apy_raw = _safe_float(r.get("borrowApy"))
            supply_apy = (supply_apy_raw * 100.0) if supply_apy_raw is not None else 0.0
            borrow_apy = (borrow_apy_raw * 100.0) if borrow_apy_raw is not None else 0.0

            supply_usd = _safe_float(r.get("totalSupplyUsd"))
            borrow_usd = _safe_float(r.get("totalBorrowUsd"))
            ltv_pct = _ltv_pct(r.get("maxLtv"))

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
                model_type="kamino-lend-variable",
                current_supply_rate_pct=supply_apy,
                current_borrow_rate_pct=borrow_apy,
            )

            # kToken receipt (applies to kToken reserves)
            receipt: ReceiptTokenInfo | None = None
            if is_ktoken:
                receipt = ReceiptTokenInfo(
                    produces_receipt_token=True,
                    receipt_token_symbol=f"k{symbol_raw}",
                    is_transferable=True,
                    is_composable=True,
                    notes="Kamino kToken — CLMM LP position used as collateral",
                )

            market_id = f"{market_address}:{r.get('reserve', symbol_raw)}"
            source_url = f"https://app.kamino.finance/lending/{market_address}"
            opp_type = OpportunityType.LENDING

            supply_rewards: list[RewardBreakdown] = [
                RewardBreakdown(
                    reward_type=RewardType.NATIVE_YIELD,
                    apy_pct=supply_apy,
                    is_variable=True,
                    notes="Variable supply APY",
                ),
            ]

            # SUPPLY
            results.append(self.build_opportunity(
                asset_id=canonical,
                asset_symbol=symbol_raw,
                chain=Chain.SOLANA.value,
                market_id=market_id,
                market_name=f"{market_name} — {symbol_raw}",
                side=OpportunitySide.SUPPLY,
                opportunity_type=opp_type,
                effective_duration=EffectiveDuration.VARIABLE,
                total_apy_pct=supply_apy,
                base_apy_pct=supply_apy,
                reward_breakdown=supply_rewards,
                total_supplied_usd=supply_usd,
                tvl_usd=supply_usd,
                liquidity=liquidity,
                rate_model=rate_model,
                is_collateral_eligible=ltv_pct is not None,
                as_collateral_max_ltv_pct=ltv_pct,
                is_amm_lp=is_ktoken,
                receipt_token=receipt,
                source_url=source_url,
            ))

            # BORROW — only when there are collateral options
            collateral_options = [
                info for cid, info in collateral_map.items()
                if cid != canonical
            ]
            if collateral_options:
                borrow_rewards: list[RewardBreakdown] = [
                    RewardBreakdown(
                        reward_type=RewardType.NATIVE_YIELD,
                        apy_pct=borrow_apy,
                        is_variable=True,
                        notes="Variable borrow APY (cost)",
                    ),
                ]
                results.append(self.build_opportunity(
                    asset_id=canonical,
                    asset_symbol=symbol_raw,
                    chain=Chain.SOLANA.value,
                    market_id=f"{market_id}:borrow",
                    market_name=f"{market_name} — {symbol_raw}",
                    side=OpportunitySide.BORROW,
                    opportunity_type=opp_type,
                    effective_duration=EffectiveDuration.VARIABLE,
                    total_apy_pct=borrow_apy,
                    base_apy_pct=borrow_apy,
                    reward_breakdown=borrow_rewards,
                    total_borrowed_usd=borrow_usd,
                    liquidity=liquidity,
                    rate_model=rate_model,
                    is_amm_lp=is_ktoken,
                    collateral_options=collateral_options,
                    source_url=source_url,
                ))

        return results

    # -- Liquidity vaults (DeFiLlama) -----------------------------------------

    async def _fetch_liquidity_vaults(self, symbols: list[str] | None) -> list[MarketOpportunity]:
        data = await get_json(settings.defillama_yields_url)
        pools: list[dict] = data.get("data", [])

        results: list[MarketOpportunity] = []
        for pool in pools:
            if pool.get("project") != "kamino-liquidity":
                continue
            if (pool.get("chain") or "").upper() != "SOLANA":
                continue
            tvl_usd = _safe_float(pool.get("tvlUsd"))
            if not tvl_usd or tvl_usd < 10_000:
                continue

            pool_symbol = pool.get("symbol", "")
            if not pool_symbol:
                continue

            apy_pct = _safe_float(pool.get("apy")) or 0.0
            pool_id = pool.get("pool", pool_symbol)

            # Normalize: use first token in pair as primary asset_id
            # e.g. "USDS-USDC" -> try "USDS", fallback to full symbol
            parts = pool_symbol.split("-")
            primary_raw = parts[0] if parts else pool_symbol
            primary_canonical = self.normalize_symbol(primary_raw, chain=Chain.SOLANA)

            if symbols and primary_canonical not in symbols and pool_symbol not in symbols:
                continue

            # Underlying tokens for context
            underlying = pool.get("underlyingTokens") or []
            pair_note = f"Kamino CLMM LP: {pool_symbol}"
            if len(underlying) == 2:
                pair_note = f"Kamino CLMM LP {pool_symbol} ({underlying[0][:8]}…/{underlying[1][:8]}…)"

            receipt = ReceiptTokenInfo(
                produces_receipt_token=True,
                receipt_token_symbol=f"k{pool_symbol.replace('-', '')}",
                is_transferable=True,
                is_composable=True,
                notes=pair_note,
            )

            results.append(self.build_opportunity(
                asset_id=primary_canonical,
                asset_symbol=pool_symbol,
                chain=Chain.SOLANA.value,
                market_id=f"kamino-liquidity:{pool_id}",
                market_name=f"Kamino Liquidity {pool_symbol}",
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
                        notes="CLMM LP fees + rebalancing yield",
                    )
                ],
                tvl_usd=tvl_usd,
                total_supplied_usd=tvl_usd,
                liquidity=LiquidityInfo(available_liquidity_usd=tvl_usd),
                is_collateral_eligible=False,
                is_amm_lp=True,
                receipt_token=receipt,
                tags=["kamino-liquidity", "clmm"],
                source_url="https://app.kamino.finance/liquidity",
            ))

        return results

    # -- Health check ----------------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        try:
            data = await get_json(
                f"{settings.kamino_api_url}/v2/kamino-market",
                params={"env": "mainnet-beta"},
            )
            markets = data if isinstance(data, list) else data.get("markets", [])
            ok = len(markets) > 0
            return {"status": "ok" if ok else "degraded", "last_success": self._last_success, "error": None}
        except Exception as exc:
            return {"status": "down", "last_success": self._last_success, "error": str(exc)}
