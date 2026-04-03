"""
Morpho adapter — covers both Morpho Blue isolated markets and MetaMorpho vaults.

Data source: https://blue-api.morpho.org/graphql  (public, no API key)

Two product types are emitted:

**Morpho Blue markets** (opportunity_type = LENDING):
  - Each market is a unique (loan token, collateral token, oracle, IRM, LLTV) tuple.
  - SUPPLY: deposit the loan token to earn interest; deposits are NOT collateral.
  - BORROW: post the collateral token, borrow the loan token.
    Each market has exactly ONE collateral asset — ``collateral_options`` will
    always contain a single :class:`CollateralAssetInfo`.
  - LLTV is the sole LTV parameter. We expose it as ``liquidation_ltv_pct``
    and use LLTV × 0.95 as the conservative ``max_ltv_pct``.

**MetaMorpho vaults** (opportunity_type = VAULT):
  - Curated by a risk manager; allocate depositor funds across Blue markets.
  - SUPPLY side only.
  - Vault shares are ERC-4626, transferable, composable as collateral elsewhere.
  - APY reported as ``netApy`` (after performance fee) where available,
    falling back to gross ``apy``.

Supported chains: Ethereum (chainId 1), Base (chainId 8453).
All APY values from the API are decimal fractions (0.05 = 5%).
They are multiplied by 100 for storage.
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
# Chain mapping
# ---------------------------------------------------------------------------

_CHAIN_ID_TO_ENUM: dict[int, Chain] = {
    1: Chain.ETHEREUM,
    8453: Chain.BASE,
}

_CHAIN_ENUM_TO_ID: dict[Chain, int] = {v: k for k, v in _CHAIN_ID_TO_ENUM.items()}

# ---------------------------------------------------------------------------
# GraphQL queries
# ---------------------------------------------------------------------------

_BLUE_MARKETS_QUERY = """
query MorphoBlueMarkets($chainIds: [Int!]!) {
  markets(
    first: 500
    orderBy: TotalSupplyUsd
    orderDirection: Desc
    where: { chainId_in: $chainIds }
  ) {
    items {
      uniqueKey
      lltv
      chain { id }
      loanAsset {
        symbol
        address
        decimals
      }
      collateralAsset {
        symbol
        address
        decimals
      }
      state {
        supplyAssets
        borrowAssets
        liquidityAssets
        supplyAssetsUsd
        borrowAssetsUsd
        liquidityAssetsUsd
        supplyApy
        borrowApy
        utilization
      }
    }
  }
}
"""

_VAULTS_QUERY = """
query MetaMorphoVaults($chainIds: [Int!]!) {
  vaults(
    first: 200
    orderBy: TotalAssetsUsd
    orderDirection: Desc
    where: { chainId_in: $chainIds }
  ) {
    items {
      address
      name
      symbol
      chain { id }
      asset {
        symbol
        address
        decimals
      }
      state {
        totalAssets
        totalAssetsUsd
        apy
        netApy
        fee
      }
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


def _lltv_to_float(lltv_str: str | None) -> float:
    """Convert a 1e18-scaled LLTV string to a decimal fraction."""
    if not lltv_str:
        return 0.0
    try:
        return int(lltv_str) / 1e18
    except (ValueError, TypeError):
        return 0.0


def _chain_from_response(item: dict) -> Chain:
    """Extract Chain enum from a market/vault dict that contains chain.id."""
    chain_id = _safe_float(item.get("chain"), "id")
    if chain_id is not None:
        return _CHAIN_ID_TO_ENUM.get(int(chain_id), Chain.ETHEREUM)
    return Chain.ETHEREUM


def _resolve_fetch_chain_ids(chains: list[Chain] | None) -> list[int]:
    supported = list(_CHAIN_ENUM_TO_ID.keys())
    if chains:
        return [_CHAIN_ENUM_TO_ID[c] for c in chains if c in _CHAIN_ENUM_TO_ID]
    return [_CHAIN_ENUM_TO_ID[c] for c in supported]


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class MorphoAdapter(ProtocolAdapter):
    """Morpho adapter emitting Morpho Blue and MetaMorpho opportunities."""

    @property
    def venue(self) -> Venue:
        return Venue.MORPHO

    @property
    def protocol_name(self) -> str:
        return "Morpho"

    @property
    def protocol_slug(self) -> str:
        return "morpho"

    @property
    def supported_chains(self) -> list[Chain]:
        return [Chain.ETHEREUM, Chain.BASE]

    @property
    def refresh_interval_seconds(self) -> int:
        return 300

    @property
    def requires_api_key(self) -> bool:
        return False

    @property
    def api_key_env_var(self) -> str | None:
        return None

    def __init__(self) -> None:
        super().__init__()
        self._api_url = settings.morpho_api_url

    # -- GraphQL helpers -------------------------------------------------------

    async def _graphql(self, query: str, variables: dict) -> dict:
        body = await post_json(self._api_url, data={"query": query, "variables": variables})
        if "errors" in body:
            raise ValueError(f"Morpho API errors: {body['errors']}")
        return body.get("data", body)

    # -- Morpho Blue markets ---------------------------------------------------

    async def _fetch_blue_opportunities(
        self,
        chain_ids: list[int],
        symbols: list[str] | None,
    ) -> list[MarketOpportunity]:
        data = await self._graphql(_BLUE_MARKETS_QUERY, {"chainIds": chain_ids})
        items = data.get("markets", {}).get("items", [])
        log.debug("morpho_blue_markets_raw", count=len(items))

        opportunities: list[MarketOpportunity] = []
        for raw in items:
            try:
                opps = self._parse_blue_market(raw)
                for opp in opps:
                    if symbols and opp.asset_id not in symbols:
                        continue
                    opportunities.append(opp)
            except Exception as exc:
                market_id = raw.get("uniqueKey", "?")
                log.warning("morpho_blue_parse_error", market=market_id, error=str(exc))

        return opportunities

    def _parse_blue_market(self, raw: dict) -> list[MarketOpportunity]:
        """Parse one Morpho Blue market into SUPPLY + BORROW opportunities."""
        unique_key = raw.get("uniqueKey", "")
        lltv_str = raw.get("lltv", "0")
        chain = _chain_from_response(raw)

        loan_asset = raw.get("loanAsset") or {}
        collateral_asset = raw.get("collateralAsset") or {}
        state = raw.get("state") or {}

        loan_symbol_raw = loan_asset.get("symbol", "")
        collateral_symbol_raw = collateral_asset.get("symbol", "") if collateral_asset else ""

        # Skip AMM LP tokens on either leg
        if self.detect_and_skip_amm_lp(loan_symbol_raw):
            return []
        if collateral_symbol_raw and self.detect_and_skip_amm_lp(collateral_symbol_raw):
            return []

        loan_canonical = self.normalize_symbol(loan_symbol_raw, chain=chain)
        collateral_canonical = (
            self.normalize_symbol(collateral_symbol_raw, chain=chain)
            if collateral_symbol_raw else None
        )

        lltv = _lltv_to_float(lltv_str)
        liq_ltv_pct = lltv * 100.0
        max_ltv_pct = lltv * 95.0  # conservative: LLTV × 0.95

        # State values — APY is decimal fraction
        supply_apy_raw = _safe_float(state, "supplyApy")
        borrow_apy_raw = _safe_float(state, "borrowApy")
        supply_apy_pct = (supply_apy_raw * 100.0) if supply_apy_raw is not None else 0.0
        borrow_apy_pct = (borrow_apy_raw * 100.0) if borrow_apy_raw is not None else 0.0

        total_supply_usd = _safe_float(state, "supplyAssetsUsd")
        total_borrow_usd = _safe_float(state, "borrowAssetsUsd")
        liquidity_usd = _safe_float(state, "liquidityAssetsUsd")
        total_supply_native = _safe_float(state, "supplyAssets")
        total_borrow_native = _safe_float(state, "borrowAssets")
        util = _safe_float(state, "utilization")
        util_pct = util * 100.0 if util is not None else None

        market_id = unique_key
        loan_address = loan_asset.get("address", "")
        market_label = (
            f"{collateral_symbol_raw}/{loan_symbol_raw}"
            if collateral_symbol_raw else loan_symbol_raw
        )

        liquidity_info = LiquidityInfo(
            available_liquidity_usd=liquidity_usd,
            utilization_rate_pct=util_pct,
        )
        rate_model = RateModelInfo(
            model_type="morpho-blue-irm",
            current_supply_rate_pct=supply_apy_pct,
            current_borrow_rate_pct=borrow_apy_pct,
        )
        source_url = f"https://app.morpho.org/market?id={unique_key}"

        results: list[MarketOpportunity] = []

        # -- SUPPLY opportunity -----------------------------------------------
        # Skip markets with no meaningful supply
        if supply_apy_pct > 0 or (total_supply_usd is not None and total_supply_usd > 0):
            supply_rewards = [
                RewardBreakdown(
                    reward_type=RewardType.NATIVE_YIELD,
                    apy_pct=supply_apy_pct,
                    is_variable=True,
                    notes="Variable supply APY",
                ),
            ]
            results.append(self.build_opportunity(
                asset_id=loan_canonical,
                asset_symbol=loan_symbol_raw,
                chain=chain.value,
                market_id=market_id,
                market_name=f"Morpho {market_label}",
                side=OpportunitySide.SUPPLY,
                opportunity_type=OpportunityType.LENDING,
                effective_duration=EffectiveDuration.VARIABLE,
                total_apy_pct=supply_apy_pct,
                base_apy_pct=supply_apy_pct,
                reward_breakdown=supply_rewards,
                total_supplied=total_supply_native,
                total_supplied_usd=total_supply_usd,
                tvl_usd=total_supply_usd,
                liquidity=liquidity_info,
                rate_model=rate_model,
                # Supply in Morpho Blue is NOT collateral-eligible
                is_collateral_eligible=False,
                source_url=source_url,
                protocol_slug="morpho-blue",
            ))

        # -- BORROW opportunity -----------------------------------------------
        if borrow_apy_pct > 0 and collateral_canonical:
            # Single collateral asset per Morpho Blue market
            collateral_entry = CollateralAssetInfo(
                asset_id=collateral_canonical,
                max_ltv_pct=max_ltv_pct,
                liquidation_ltv_pct=liq_ltv_pct,
                is_isolated=True,  # Morpho Blue is always isolated pair
            )
            borrow_rewards = [
                RewardBreakdown(
                    reward_type=RewardType.NATIVE_YIELD,
                    apy_pct=borrow_apy_pct,
                    is_variable=True,
                    notes="Variable borrow APY (cost)",
                ),
            ]
            results.append(self.build_opportunity(
                asset_id=loan_canonical,
                asset_symbol=loan_symbol_raw,
                chain=chain.value,
                market_id=f"{market_id}:borrow",
                market_name=f"Morpho {market_label}",
                side=OpportunitySide.BORROW,
                opportunity_type=OpportunityType.LENDING,
                effective_duration=EffectiveDuration.VARIABLE,
                total_apy_pct=borrow_apy_pct,
                base_apy_pct=borrow_apy_pct,
                reward_breakdown=borrow_rewards,
                total_borrowed=total_borrow_native,
                total_borrowed_usd=total_borrow_usd,
                liquidity=liquidity_info,
                rate_model=rate_model,
                collateral_options=[collateral_entry],
                source_url=source_url,
                protocol_slug="morpho-blue",
            ))

        return results

    # -- MetaMorpho vaults -----------------------------------------------------

    async def _fetch_vault_opportunities(
        self,
        chain_ids: list[int],
        symbols: list[str] | None,
    ) -> list[MarketOpportunity]:
        data = await self._graphql(_VAULTS_QUERY, {"chainIds": chain_ids})
        items = data.get("vaults", {}).get("items", [])
        log.debug("metamorpho_vaults_raw", count=len(items))

        opportunities: list[MarketOpportunity] = []
        for raw in items:
            try:
                opp = self._parse_vault(raw)
                if opp is None:
                    continue
                if symbols and opp.asset_id not in symbols:
                    continue
                opportunities.append(opp)
            except Exception as exc:
                vault_addr = raw.get("address", "?")
                log.warning("metamorpho_vault_parse_error", vault=vault_addr, error=str(exc))

        return opportunities

    def _parse_vault(self, raw: dict) -> MarketOpportunity | None:
        """Parse one MetaMorpho vault into a SUPPLY opportunity."""
        vault_address = raw.get("address", "")
        vault_name = raw.get("name", "")
        vault_symbol = raw.get("symbol", "")
        chain = _chain_from_response(raw)

        asset = raw.get("asset") or {}
        state = raw.get("state") or {}

        asset_symbol_raw = asset.get("symbol", "")
        if not asset_symbol_raw:
            return None

        if self.detect_and_skip_amm_lp(asset_symbol_raw):
            return None

        asset_canonical = self.normalize_symbol(asset_symbol_raw, chain=chain)

        # Use netApy (after fee) if available, fall back to gross apy
        net_apy_raw = _safe_float(state, "netApy")
        gross_apy_raw = _safe_float(state, "apy")
        apy_raw = net_apy_raw if net_apy_raw is not None else gross_apy_raw
        apy_pct = (apy_raw * 100.0) if apy_raw is not None else 0.0

        total_assets_usd = _safe_float(state, "totalAssetsUsd")
        total_assets_native = _safe_float(state, "totalAssets")
        fee = _safe_float(state, "fee")

        market_id = vault_address
        market_label = vault_name or vault_symbol or f"MetaMorpho {asset_symbol_raw}"

        # ERC-4626 vault shares
        receipt = ReceiptTokenInfo(
            produces_receipt_token=True,
            receipt_token_symbol=vault_symbol or f"mm{asset_symbol_raw}",
            is_transferable=True,
            is_composable=True,
            notes="ERC-4626 MetaMorpho vault share — composable as collateral",
        )

        rewards = [
            RewardBreakdown(
                reward_type=RewardType.NATIVE_YIELD,
                apy_pct=apy_pct,
                is_variable=True,
                notes=f"Blended net APY across allocated Morpho Blue markets"
                + (f" (after {fee * 100:.1f}% performance fee)" if fee else ""),
            ),
        ]

        tags: list[str] = ["metamorpho"]
        if fee and fee > 0:
            tags.append(f"fee:{fee * 100:.1f}%")

        return self.build_opportunity(
            asset_id=asset_canonical,
            asset_symbol=asset_symbol_raw,
            chain=chain.value,
            market_id=market_id,
            market_name=market_label,
            side=OpportunitySide.SUPPLY,
            opportunity_type=OpportunityType.VAULT,
            effective_duration=EffectiveDuration.OVERNIGHT,
            total_apy_pct=apy_pct,
            base_apy_pct=apy_pct,
            reward_breakdown=rewards,
            total_supplied=total_assets_native,
            total_supplied_usd=total_assets_usd,
            tvl_usd=total_assets_usd,
            liquidity=LiquidityInfo(available_liquidity_usd=total_assets_usd),
            receipt_token=receipt,
            tags=tags,
            source_url=f"https://app.morpho.org/vault?vault={vault_address}",
            protocol_slug="metamorpho",
        )

    # -- ProtocolAdapter interface ---------------------------------------------

    async def fetch_opportunities(
        self,
        symbols: list[str] | None = None,
        chains: list[Chain] | None = None,
    ) -> list[MarketOpportunity]:
        """Fetch Morpho Blue markets and MetaMorpho vaults."""
        effective_chains = [c for c in (chains or self.supported_chains) if c in _CHAIN_ENUM_TO_ID]
        chain_ids = [_CHAIN_ENUM_TO_ID[c] for c in effective_chains]
        # Set of accepted chain values for client-side guard
        accepted_chain_values = {c.value for c in effective_chains}

        blue_opps, vault_opps = await asyncio.gather(
            self._fetch_blue_opportunities(chain_ids, symbols),
            self._fetch_vault_opportunities(chain_ids, symbols),
            return_exceptions=True,
        )

        all_opps: list[MarketOpportunity] = []

        if isinstance(blue_opps, Exception):
            log.error("morpho_blue_fetch_error", error=str(blue_opps))
        else:
            all_opps.extend(blue_opps)

        if isinstance(vault_opps, Exception):
            log.error("metamorpho_vault_fetch_error", error=str(vault_opps))
        else:
            all_opps.extend(vault_opps)

        # Client-side guard: drop any items the API returned from unintended chains
        if accepted_chain_values:
            all_opps = [o for o in all_opps if o.chain in accepted_chain_values]

        n_vault = sum(1 for o in all_opps if o.opportunity_type.value == "VAULT")
        log.info(
            "morpho_fetch_done",
            total=len(all_opps),
            blue=len(all_opps) - n_vault,
            vaults=n_vault,
        )
        return all_opps

    async def health_check(self) -> dict[str, Any]:
        try:
            body = await post_json(
                self._api_url,
                data={"query": "{ markets(first: 1) { items { uniqueKey } } }"},
            )
            ok = "data" in body
            return {"status": "ok" if ok else "degraded", "last_success": self._last_success, "error": None}
        except Exception as exc:
            return {"status": "down", "last_success": self._last_success, "error": str(exc)}
