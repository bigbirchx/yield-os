"""
Euler V2 adapter.

Architecture
────────────
Euler V2 is a modular, permissionless lending protocol.  Each *EVault* is an
ERC-4626 vault that acts as an isolated lending market with its own parameters.

Within each EVault:
  - Suppliers deposit an asset and receive ERC-4626 shares → SUPPLY opportunity
  - Borrowers draw against collateral from separate vaults → BORROW opportunity
  - A vault's ``collateralLTV`` map determines which assets can collateralise it

Data source
───────────
Euler's public GraphQL API (``euler_v2_url`` setting).

Key GraphQL fields:
  ``supplyAPY`` / ``borrowAPY`` — decimal fractions (0.05 = 5%), converted ×100
  ``totalSupplyAssets`` / ``totalBorrowAssets`` — in asset token units
  ``totalSupplyAssetsUSD`` / ``totalBorrowAssetsUSD`` — USD
  ``supplyCap`` / ``borrowCap`` — in asset token units (0 = uncapped)
  ``collateralLTVs`` — list of {collateral {id symbol}, borrowLTV, liquidationLTV}
  ``chainId`` — int (1=Ethereum, 42161=Arbitrum, 8453=Base)

Supported chains: ETHEREUM, ARBITRUM, BASE.
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
    42161: Chain.ARBITRUM,
    8453: Chain.BASE,
}

_CHAIN_ENUM_TO_ID: dict[Chain, int] = {v: k for k, v in _CHAIN_ID_TO_ENUM.items()}

# ---------------------------------------------------------------------------
# GraphQL query
# ---------------------------------------------------------------------------

_VAULTS_QUERY = """
query EulerVaults($chainIds: [Int!]!) {
  vaults(
    where: { chainId_in: $chainIds, isActive: true }
    orderBy: totalSupplyAssetsUSD
    orderDirection: desc
    first: 300
  ) {
    id
    address
    name
    symbol
    chainId
    asset {
      id
      symbol
      decimals
    }
    supplyAPY
    borrowAPY
    totalSupplyAssets
    totalSupplyAssetsUSD
    totalBorrowAssets
    totalBorrowAssetsUSD
    supplyCap
    borrowCap
    collateralLTVs {
      collateral {
        id
        address
        symbol
        totalSupplyAssetsUSD
        maximumLTV
        liquidationLTV
      }
      borrowLTV
      liquidationLTV
    }
  }
}
"""

# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _cap_remaining(total_supply: float | None, cap: Any) -> tuple[float | None, float | None, bool]:
    """Return (cap_usd, remaining_usd, is_capped).

    ``cap`` comes as a token-unit string from the API.  We cannot convert it
    to USD without a price oracle.  If it is non-zero we mark the vault as
    capacity-capped but leave the USD amounts as None.
    """
    try:
        cap_raw = int(cap)
    except (TypeError, ValueError):
        cap_raw = 0
    if cap_raw == 0:
        return None, None, False
    return None, None, True  # capped; USD amounts unavailable without oracle


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class EulerV2Adapter(ProtocolAdapter):
    """Euler V2 EVault adapter — multi-chain via Euler's GraphQL API."""

    @property
    def venue(self) -> Venue:
        return Venue.EULER_V2

    @property
    def protocol_name(self) -> str:
        return "Euler V2"

    @property
    def protocol_slug(self) -> str:
        return "euler-v2"

    @property
    def supported_chains(self) -> list[Chain]:
        return [Chain.ETHEREUM, Chain.ARBITRUM, Chain.BASE]

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
        effective_chains = [c for c in (chains or self.supported_chains) if c in _CHAIN_ENUM_TO_ID]
        chain_ids = [_CHAIN_ENUM_TO_ID[c] for c in effective_chains]

        body = await post_json(
            settings.euler_v2_url,
            data={"query": _VAULTS_QUERY, "variables": {"chainIds": chain_ids}},
        )
        if "errors" in body:
            raise ValueError(f"Euler V2 API error: {body['errors']}")

        vaults = body.get("data", {}).get("vaults", [])

        # Client-side chain guard
        accepted_chain_ids = set(chain_ids)

        all_opps: list[MarketOpportunity] = []
        for vault in vaults:
            chain_id = vault.get("chainId")
            if chain_id not in accepted_chain_ids:
                continue
            chain = _CHAIN_ID_TO_ENUM.get(chain_id)
            if chain is None:
                continue
            try:
                opps = self._parse_vault(vault, chain)
                for opp in opps:
                    if symbols and opp.asset_id not in symbols:
                        continue
                    all_opps.append(opp)
            except Exception as exc:
                vault_id = vault.get("id", "unknown")
                log.warning("euler_v2_vault_error", vault=vault_id, chain=chain.value, error=str(exc))

        log.info("euler_v2_fetch_done", total=len(all_opps))
        return all_opps

    # -- Vault parser ----------------------------------------------------------

    def _parse_vault(self, vault: dict, chain: Chain) -> list[MarketOpportunity]:
        asset = vault.get("asset") or {}
        symbol_raw = asset.get("symbol", "")
        if not symbol_raw or self.detect_and_skip_amm_lp(symbol_raw):
            return []

        canonical = self.normalize_symbol(symbol_raw, chain=chain)
        vault_id = vault.get("id") or vault.get("address", "unknown")
        vault_name = vault.get("name") or f"Euler {symbol_raw}"
        vault_symbol = vault.get("symbol") or f"e{symbol_raw}"

        # APY — decimal fractions → percent
        supply_apy_raw = _safe_float(vault.get("supplyAPY"))
        borrow_apy_raw = _safe_float(vault.get("borrowAPY"))
        supply_apy = (supply_apy_raw * 100.0) if supply_apy_raw is not None else 0.0
        borrow_apy = (borrow_apy_raw * 100.0) if borrow_apy_raw is not None else 0.0

        # Sizes
        supply_usd = _safe_float(vault.get("totalSupplyAssetsUSD"))
        borrow_usd = _safe_float(vault.get("totalBorrowAssetsUSD"))

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
            model_type="euler-v2-evault",
            current_supply_rate_pct=supply_apy,
            current_borrow_rate_pct=borrow_apy,
        )

        receipt = ReceiptTokenInfo(
            produces_receipt_token=True,
            receipt_token_symbol=vault_symbol,
            is_transferable=True,
            is_composable=True,
            notes="Euler V2 ERC-4626 vault share",
        )

        # Supply cap
        _, _, supply_capped = _cap_remaining(supply_usd, vault.get("supplyCap"))

        # Collateral matrix for BORROW side
        collateral_options = self._build_collateral_options(vault.get("collateralLTVs") or [], chain)

        market_id = vault_id
        source_url = f"https://app.euler.finance/vault/{vault.get('address', vault_id)}"

        supply_rewards: list[RewardBreakdown] = [
            RewardBreakdown(
                reward_type=RewardType.NATIVE_YIELD,
                apy_pct=supply_apy,
                is_variable=True,
                notes="Variable supply APY",
            ),
        ]

        results: list[MarketOpportunity] = []

        # -- SUPPLY --
        results.append(self.build_opportunity(
            asset_id=canonical,
            asset_symbol=symbol_raw,
            chain=chain.value,
            market_id=market_id,
            market_name=vault_name,
            side=OpportunitySide.SUPPLY,
            opportunity_type=OpportunityType.LENDING,
            effective_duration=EffectiveDuration.VARIABLE,
            total_apy_pct=supply_apy,
            base_apy_pct=supply_apy,
            reward_breakdown=supply_rewards,
            total_supplied_usd=supply_usd,
            tvl_usd=supply_usd,
            liquidity=liquidity,
            rate_model=rate_model,
            is_capacity_capped=supply_capped,
            is_collateral_eligible=bool(collateral_options),
            receipt_token=receipt,
            source_url=source_url,
        ))

        # -- BORROW (only if there are configured collateral assets) --
        if collateral_options:
            _, _, borrow_capped = _cap_remaining(borrow_usd, vault.get("borrowCap"))
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
                chain=chain.value,
                market_id=f"{market_id}:borrow",
                market_name=vault_name,
                side=OpportunitySide.BORROW,
                opportunity_type=OpportunityType.LENDING,
                effective_duration=EffectiveDuration.VARIABLE,
                total_apy_pct=borrow_apy,
                base_apy_pct=borrow_apy,
                reward_breakdown=borrow_rewards,
                total_borrowed_usd=borrow_usd,
                liquidity=liquidity,
                rate_model=rate_model,
                is_capacity_capped=borrow_capped,
                collateral_options=collateral_options,
                source_url=source_url,
            ))

        return results

    def _build_collateral_options(
        self,
        collateral_ltvs: list[dict],
        chain: Chain,
    ) -> list[CollateralAssetInfo]:
        options = []
        for entry in collateral_ltvs:
            collateral = entry.get("collateral") or {}
            symbol_raw = collateral.get("symbol", "")
            if not symbol_raw or self.detect_and_skip_amm_lp(symbol_raw):
                continue
            borrow_ltv = _safe_float(entry.get("borrowLTV"))
            liq_ltv = _safe_float(entry.get("liquidationLTV"))
            if not borrow_ltv or borrow_ltv <= 0:
                continue
            canonical = self.normalize_symbol(symbol_raw, chain=chain)
            tvl = _safe_float(collateral.get("totalSupplyAssetsUSD"))
            options.append(CollateralAssetInfo(
                asset_id=canonical,
                max_ltv_pct=borrow_ltv * 100.0,
                liquidation_ltv_pct=(liq_ltv * 100.0) if liq_ltv else borrow_ltv * 100.0,
                current_deposits=tvl,
            ))
        return options

    async def health_check(self) -> dict[str, Any]:
        try:
            body = await post_json(
                settings.euler_v2_url,
                data={"query": "{ vaults(first: 1) { id } }"},
            )
            ok = bool(body.get("data", {}).get("vaults"))
            return {"status": "ok" if ok else "degraded", "last_success": self._last_success, "error": None}
        except Exception as exc:
            return {"status": "down", "last_success": self._last_success, "error": str(exc)}
