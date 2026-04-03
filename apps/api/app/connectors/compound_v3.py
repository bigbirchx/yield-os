"""
Compound V3 (Comet) adapter.

Architecture
────────────
Each Compound V3 deployment is a *Comet* — a single-base-asset market.
For example, the Ethereum USDC Comet and the Ethereum WETH Comet are separate.

Within each Comet:
  - The **base asset** (e.g. USDC) can be:
      • Supplied to earn the variable supply APY              → SUPPLY opportunity
      • Borrowed against posted collateral                    → BORROW opportunity
  - **Collateral assets** can only be posted to enable borrowing;
    they earn **no interest** in Compound V3.  They appear exclusively
    as entries in ``collateral_options`` on the BORROW opportunity.

Data source
───────────
Messari's Compound V3 DeFi Schema subgraphs (one per chain).

The Messari DeFi Schema represents each asset within a Comet as a separate
*market* row.  Markets are grouped by ``protocol.id`` (= the Comet address):

  ``canBorrowFrom = True``  → base asset market  (supply / borrow)
  ``canUseAsCollateral = True`` → collateral asset market (matrix only)

APY values in Messari schema are stored as percentages (5.0 = 5%).
COMP reward APY uses ``type = "REWARD"`` rate entries.

Supported chains: ETHEREUM, ARBITRUM, BASE, POLYGON, OPTIMISM.
"""
from __future__ import annotations

import asyncio
from typing import Any

import structlog

from app.connectors.base_adapter import ProtocolAdapter
from app.connectors.http_client import post_json
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
# Chain config — subgraph URL + chain value
# ---------------------------------------------------------------------------

# Built lazily from settings so tests can override
def _chain_urls() -> dict[Chain, str]:
    return {
        Chain.ETHEREUM: settings.compound_v3_ethereum_url,
        Chain.ARBITRUM: settings.compound_v3_arbitrum_url,
        Chain.BASE: settings.compound_v3_base_url,
        Chain.POLYGON: settings.compound_v3_polygon_url,
        Chain.OPTIMISM: settings.compound_v3_optimism_url,
    }


# ---------------------------------------------------------------------------
# GraphQL query — Messari DeFi Schema for lending protocols
# ---------------------------------------------------------------------------

_MARKETS_QUERY = """
{
  markets(
    orderBy: totalValueLockedUSD
    orderDirection: desc
    first: 200
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
    totalBorrowBalanceUSD
    maximumLTV
    liquidationThreshold
    liquidationPenalty
    canBorrowFrom
    canUseAsCollateral
    isActive
    rewardTokens {
      id
      symbol
    }
    rewardTokenEmissionsUSD
    protocol {
      id
      name
    }
  }
}
"""

# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _safe_float(obj: dict | None, *keys: str) -> float | None:
    if obj is None:
        return None
    cur: Any = obj
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
        if cur is None:
            return None
    try:
        return float(cur)
    except (ValueError, TypeError):
        return None


def _extract_rate(rates: list[dict], side: str, rate_type: str = "VARIABLE") -> float | None:
    """Pull a specific rate out of the Messari rates array."""
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


class CompoundV3Adapter(ProtocolAdapter):
    """Compound V3 (Comet) adapter — multi-chain via per-chain Messari subgraphs."""

    @property
    def venue(self) -> Venue:
        return Venue.COMPOUND_V3

    @property
    def protocol_name(self) -> str:
        return "Compound V3"

    @property
    def protocol_slug(self) -> str:
        return "compound-v3"

    @property
    def supported_chains(self) -> list[Chain]:
        return [
            Chain.ETHEREUM,
            Chain.ARBITRUM,
            Chain.BASE,
            Chain.POLYGON,
            Chain.OPTIMISM,
        ]

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
        target = [c for c in (chains or self.supported_chains) if c in _chain_urls()]
        tasks = {c: self._fetch_chain(c, symbols) for c in target}

        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        all_opps: list[MarketOpportunity] = []
        for chain, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                log.warning("compound_v3_chain_error", chain=chain.value, error=str(result))
            else:
                all_opps.extend(result)

        log.info("compound_v3_fetch_done", total=len(all_opps), chains=[c.value for c in target])
        return all_opps

    async def _fetch_chain(
        self,
        chain: Chain,
        symbols: list[str] | None,
    ) -> list[MarketOpportunity]:
        url = _chain_urls()[chain]
        body = await post_json(url, data={"query": _MARKETS_QUERY})
        if "errors" in body:
            raise ValueError(f"Compound V3 subgraph error ({chain.value}): {body['errors']}")
        markets = body.get("data", {}).get("markets", [])

        # Group markets by Comet deployment (protocol.id)
        comets: dict[str, list[dict]] = {}
        for m in markets:
            comet_id = (m.get("protocol") or {}).get("id") or m.get("id", "unknown")
            comets.setdefault(comet_id, []).append(m)

        all_opps: list[MarketOpportunity] = []
        for comet_id, comet_markets in comets.items():
            try:
                opps = self._build_comet_opportunities(comet_id, comet_markets, chain)
                for opp in opps:
                    if symbols and opp.asset_id not in symbols:
                        continue
                    all_opps.append(opp)
            except Exception as exc:
                log.warning("compound_v3_comet_error", comet=comet_id, chain=chain.value, error=str(exc))

        return all_opps

    # -- Comet-level builder ---------------------------------------------------

    def _build_comet_opportunities(
        self,
        comet_id: str,
        markets: list[dict],
        chain: Chain,
    ) -> list[MarketOpportunity]:
        # Identify base (borrowable) vs collateral-only markets
        base_markets = [m for m in markets if m.get("canBorrowFrom")]
        collateral_markets = [m for m in markets if m.get("canUseAsCollateral")]

        # Inactive or purely-collateral comets have no supply/borrow opportunities
        if not base_markets:
            return []

        # Build the collateral option list for this Comet (shared across all BORROW opps)
        collateral_options = [
            opt
            for m in collateral_markets
            if (opt := self._build_collateral_option(m, chain)) is not None
        ]

        results: list[MarketOpportunity] = []
        for base in base_markets:
            if not base.get("isActive", True):
                continue
            token = base.get("inputToken") or {}
            symbol_raw = token.get("symbol", "")
            if not symbol_raw:
                continue
            if self.detect_and_skip_amm_lp(symbol_raw):
                continue

            canonical = self.normalize_symbol(symbol_raw, chain=chain)
            rates = base.get("rates") or []
            tvl_usd = _safe_float(base, "totalValueLockedUSD")
            borrow_usd = _safe_float(base, "totalBorrowBalanceUSD")
            supply_usd = tvl_usd

            # Available liquidity = supply - borrow
            avail_usd = None
            if supply_usd is not None and borrow_usd is not None:
                avail_usd = max(supply_usd - borrow_usd, 0.0)

            # Utilization
            util_pct = None
            if supply_usd and borrow_usd and supply_usd > 0:
                util_pct = min(borrow_usd / supply_usd * 100.0, 100.0)

            liquidity = LiquidityInfo(
                available_liquidity_usd=avail_usd,
                utilization_rate_pct=util_pct,
            )

            # Rates — Messari stores as percentage (5.0 = 5%)
            supply_base_apy = _extract_rate(rates, "LENDER", "VARIABLE") or 0.0
            borrow_base_apy = _extract_rate(rates, "BORROWER", "VARIABLE") or 0.0
            supply_reward_apy = _extract_rate(rates, "LENDER", "REWARD") or 0.0
            borrow_reward_apy = _extract_rate(rates, "BORROWER", "REWARD") or 0.0
            supply_total_apy = supply_base_apy + supply_reward_apy
            borrow_net_apy = max(borrow_base_apy - borrow_reward_apy, 0.0)

            # Reward token info
            reward_tokens = base.get("rewardTokens") or []
            reward_token_symbol = reward_tokens[0].get("symbol") if reward_tokens else "COMP"

            market_id = f"{comet_id}:{token.get('id', symbol_raw)}"
            output_token = base.get("outputToken") or {}
            comet_name = (base.get("protocol") or {}).get("name") or f"Compound {symbol_raw} Comet"

            rate_model = RateModelInfo(
                model_type="compound-v3-comet",
                current_supply_rate_pct=supply_base_apy,
                current_borrow_rate_pct=borrow_base_apy,
            )

            # cToken receipt token
            receipt = ReceiptTokenInfo(
                produces_receipt_token=True,
                receipt_token_symbol=output_token.get("symbol") or f"c{symbol_raw}v3",
                is_transferable=True,
                is_composable=False,
                notes="Compound V3 supply position (non-fungible, account-based)",
            )

            # -- SUPPLY --
            supply_rewards: list[RewardBreakdown] = [
                RewardBreakdown(
                    reward_type=RewardType.NATIVE_YIELD,
                    apy_pct=supply_base_apy,
                    is_variable=True,
                    notes="Base supply APY",
                ),
            ]
            if supply_reward_apy > 0:
                supply_rewards.append(RewardBreakdown(
                    reward_type=RewardType.TOKEN_INCENTIVE,
                    token_id="COMP",
                    token_name=reward_token_symbol,
                    apy_pct=supply_reward_apy,
                    is_variable=True,
                    notes="COMP token rewards",
                ))

            results.append(self.build_opportunity(
                asset_id=canonical,
                asset_symbol=symbol_raw,
                chain=chain.value,
                market_id=market_id,
                market_name=f"{comet_name} {symbol_raw}",
                side=OpportunitySide.SUPPLY,
                opportunity_type=OpportunityType.LENDING,
                effective_duration=EffectiveDuration.VARIABLE,
                total_apy_pct=supply_total_apy,
                base_apy_pct=supply_base_apy,
                reward_breakdown=supply_rewards,
                total_supplied_usd=supply_usd,
                tvl_usd=tvl_usd,
                liquidity=liquidity,
                rate_model=rate_model,
                is_collateral_eligible=False,  # V3 base supply is not collateral
                receipt_token=receipt,
                source_url=f"https://app.compound.finance/markets/{comet_id}",
            ))

            # -- BORROW --
            borrow_rewards: list[RewardBreakdown] = [
                RewardBreakdown(
                    reward_type=RewardType.NATIVE_YIELD,
                    apy_pct=borrow_base_apy,
                    is_variable=True,
                    notes="Base borrow APY (cost)",
                ),
            ]
            if borrow_reward_apy > 0:
                borrow_rewards.append(RewardBreakdown(
                    reward_type=RewardType.TOKEN_INCENTIVE,
                    token_id="COMP",
                    token_name=reward_token_symbol,
                    apy_pct=borrow_reward_apy,
                    is_variable=True,
                    notes="COMP rewards offset borrow cost",
                ))

            if collateral_options:
                results.append(self.build_opportunity(
                    asset_id=canonical,
                    asset_symbol=symbol_raw,
                    chain=chain.value,
                    market_id=f"{market_id}:borrow",
                    market_name=f"{comet_name} {symbol_raw}",
                    side=OpportunitySide.BORROW,
                    opportunity_type=OpportunityType.LENDING,
                    effective_duration=EffectiveDuration.VARIABLE,
                    total_apy_pct=borrow_net_apy,
                    base_apy_pct=borrow_base_apy,
                    reward_breakdown=borrow_rewards,
                    total_borrowed_usd=borrow_usd,
                    liquidity=liquidity,
                    rate_model=rate_model,
                    collateral_options=collateral_options,
                    source_url=f"https://app.compound.finance/markets/{comet_id}",
                ))

        return results

    def _build_collateral_option(
        self,
        market: dict,
        chain: Chain,
    ) -> CollateralAssetInfo | None:
        token = market.get("inputToken") or {}
        symbol_raw = token.get("symbol", "")
        if not symbol_raw or self.detect_and_skip_amm_lp(symbol_raw):
            return None

        canonical = self.normalize_symbol(symbol_raw, chain=chain)
        max_ltv = _safe_float(market, "maximumLTV")
        liq_threshold = _safe_float(market, "liquidationThreshold")
        tvl = _safe_float(market, "totalValueLockedUSD")

        if not max_ltv or max_ltv <= 0:
            return None

        return CollateralAssetInfo(
            asset_id=canonical,
            max_ltv_pct=max_ltv,
            liquidation_ltv_pct=liq_threshold or max_ltv,
            current_deposits=tvl,
        )

    async def health_check(self) -> dict[str, Any]:
        url = settings.compound_v3_ethereum_url
        try:
            body = await post_json(url, data={"query": "{ markets(first: 1) { id } }"})
            ok = bool(body.get("data", {}).get("markets"))
            return {"status": "ok" if ok else "degraded", "last_success": self._last_success, "error": None}
        except Exception as exc:
            return {"status": "down", "last_success": self._last_success, "error": str(exc)}
