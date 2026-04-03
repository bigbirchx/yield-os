"""
Aave V3 adapter — reference implementation of :class:`ProtocolAdapter`.

Connects to the Aave official GraphQL API (api.v3.aave.com/graphql) to
produce :class:`MarketOpportunity` instances for every active reserve on
supported chains.

For each reserve the adapter emits up to **two** opportunities:

  1. **SUPPLY** — deposit the asset to earn interest; may be used as collateral.
  2. **BORROW** — borrow the asset against deposited collateral.

Rich fields populated:
  - Rate model (base rate, slope1, slope2, optimal utilization)
  - Collateral matrix on the borrow side (every eligible collateral asset)
  - E-mode elevated LTV values
  - Isolation-mode flags
  - Receipt token metadata (aTokens)
  - Supply/borrow caps and remaining capacity

Multi-chain: Ethereum, Arbitrum, Base, Optimism (configurable).
"""
from __future__ import annotations

from datetime import UTC, datetime
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
# Chain mapping
# ---------------------------------------------------------------------------

_CHAIN_ID_TO_ENUM: dict[int, Chain] = {
    1: Chain.ETHEREUM,
    42161: Chain.ARBITRUM,
    8453: Chain.BASE,
    10: Chain.OPTIMISM,
    137: Chain.POLYGON,
    43114: Chain.AVALANCHE,
    56: Chain.BSC,
    534352: Chain.SCROLL,
    59144: Chain.LINEA,
}

_CHAIN_NAME_TO_ENUM: dict[str, Chain] = {
    "Ethereum": Chain.ETHEREUM,
    "Arbitrum": Chain.ARBITRUM,
    "Base": Chain.BASE,
    "Optimism": Chain.OPTIMISM,
    "Polygon": Chain.POLYGON,
    "Avalanche": Chain.AVALANCHE,
    "BNB Chain": Chain.BSC,
    "Scroll": Chain.SCROLL,
    "Linea": Chain.LINEA,
}


def _resolve_chain(chain_name: str, chain_id: int) -> Chain:
    """Resolve an Aave chain name/id to our Chain enum."""
    if chain_id in _CHAIN_ID_TO_ENUM:
        return _CHAIN_ID_TO_ENUM[chain_id]
    if chain_name in _CHAIN_NAME_TO_ENUM:
        return _CHAIN_NAME_TO_ENUM[chain_name]
    return Chain.ETHEREUM  # fallback


# ---------------------------------------------------------------------------
# GraphQL query — extended to include fields the legacy client omits
# ---------------------------------------------------------------------------

_MARKETS_QUERY = """
query AaveMarkets($chainIds: [ChainId!]!) {
  markets(request: { chainIds: $chainIds }) {
    name
    address
    chain { name chainId }
    reserves {
      underlyingToken { symbol address decimals }
      isFrozen
      isPaused
      isolationModeConfig { canBeCollateral }
      eModeInfo {
        label
        maxLTV { value }
        liquidationThreshold { value }
        liquidationPenalty { value }
      }
      supplyInfo {
        apy { value }
        total { value }
        maxLTV { value }
        liquidationThreshold { value }
        liquidationBonus { value }
        canBeCollateral
        supplyCap { usd amount { value } }
        supplyCapReached
      }
      borrowInfo {
        apy { value }
        total { usd amount { value } }
        borrowCap { usd amount { value } }
        borrowCapReached
        availableLiquidity { usd amount { value } }
        utilizationRate { value }
        borrowingState
        baseVariableBorrowRate { value }
        variableRateSlope1 { value }
        variableRateSlope2 { value }
        optimalUsageRate { value }
      }
    }
  }
}
"""


# ---------------------------------------------------------------------------
# Response parsing helpers
# ---------------------------------------------------------------------------


def _safe_float(obj: dict | None, *keys: str) -> float | None:
    """Drill into nested dicts and return a float, or None."""
    if obj is None:
        return None
    cursor: Any = obj
    for k in keys:
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(k)
        if cursor is None:
            return None
    try:
        return float(cursor)
    except (ValueError, TypeError):
        return None


def _safe_str(obj: dict | None, *keys: str) -> str | None:
    cursor: Any = obj
    for k in keys:
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(k)
        if cursor is None:
            return None
    return str(cursor) if cursor is not None else None


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class AaveV3Adapter(ProtocolAdapter):
    """Aave V3 multi-chain adapter.

    Fetches all reserves via the official GraphQL API, then builds supply
    and borrow :class:`MarketOpportunity` instances for every active reserve.
    """

    # -- ProtocolAdapter properties -------------------------------------------

    @property
    def venue(self) -> Venue:
        return Venue.AAVE_V3

    @property
    def protocol_name(self) -> str:
        return "Aave V3"

    @property
    def protocol_slug(self) -> str:
        return "aave-v3"

    @property
    def supported_chains(self) -> list[Chain]:
        return [
            Chain.ETHEREUM,
            Chain.ARBITRUM,
            Chain.BASE,
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

    # -- Internal helpers -----------------------------------------------------

    def __init__(self) -> None:
        super().__init__()
        self._api_url = settings.aave_api_url
        self._chain_ids = [
            int(c.strip())
            for c in settings.aave_chain_ids.split(",")
            if c.strip()
        ]

    async def _fetch_markets_raw(
        self,
        chain_ids: list[int] | None = None,
    ) -> list[dict]:
        """POST the GraphQL query and return the raw markets list."""
        ids = chain_ids or self._chain_ids
        payload = {
            "query": _MARKETS_QUERY,
            "variables": {"chainIds": ids},
        }
        body = await post_json(self._api_url, data=payload)
        if "errors" in body:
            raise ValueError(f"Aave API errors: {body['errors']}")
        data = body.get("data", body)
        return data.get("markets", [])

    def _parse_reserve(
        self,
        raw: dict,
        market_name: str,
        market_address: str,
        chain: Chain,
    ) -> list[MarketOpportunity]:
        """Parse a single reserve dict into 0-2 MarketOpportunity instances."""
        token = raw.get("underlyingToken", {})
        symbol_raw = token.get("symbol", "")
        token_address = token.get("address", "")
        is_frozen = raw.get("isFrozen", False)
        is_paused = raw.get("isPaused", False)
        iso_config = raw.get("isolationModeConfig") or {}
        is_isolated = not iso_config.get("canBeCollateral", True) if iso_config else False

        if is_frozen or is_paused:
            return []

        # Skip AMM LP tokens
        if self.detect_and_skip_amm_lp(symbol_raw):
            return []

        # Normalize symbol
        canonical = self.normalize_symbol(symbol_raw, chain=chain)
        asset_symbol = symbol_raw

        # Market ID used in opportunity_id generation
        market_id = f"{market_address}:{token_address}"

        supply_info = raw.get("supplyInfo", {})
        borrow_info = raw.get("borrowInfo", {})
        # eModeInfo is now a list; take the first entry if present
        emode_list = raw.get("eModeInfo") or []
        emode = emode_list[0] if emode_list else None

        opportunities: list[MarketOpportunity] = []

        # ── SUPPLY side ─────────────────────────────────────────────────
        supply_opp = self._build_supply(
            canonical=canonical,
            asset_symbol=asset_symbol,
            chain=chain,
            market_id=market_id,
            market_name=market_name,
            supply_info=supply_info,
            borrow_info=borrow_info,
            emode=emode,
            is_isolated=is_isolated,
            token_address=token_address,
        )
        if supply_opp is not None:
            opportunities.append(supply_opp)

        # ── BORROW side ─────────────────────────────────────────────────
        borrowing_state = _safe_str(borrow_info, "borrowingState")
        if borrowing_state == "ENABLED":
            borrow_opp = self._build_borrow(
                canonical=canonical,
                asset_symbol=asset_symbol,
                chain=chain,
                market_id=market_id,
                market_name=market_name,
                supply_info=supply_info,
                borrow_info=borrow_info,
                emode=emode,
                is_isolated=is_isolated,
            )
            if borrow_opp is not None:
                opportunities.append(borrow_opp)

        return opportunities

    def _build_supply(
        self,
        *,
        canonical: str,
        asset_symbol: str,
        chain: Chain,
        market_id: str,
        market_name: str,
        supply_info: dict,
        borrow_info: dict,
        emode: dict | None,
        is_isolated: bool,
        token_address: str,
    ) -> MarketOpportunity | None:
        """Build a SUPPLY-side MarketOpportunity."""
        supply_apy_raw = _safe_float(supply_info, "apy", "value")
        if supply_apy_raw is None:
            return None

        supply_apy_pct = supply_apy_raw * 100.0

        # LTV and liquidation params (decimal fractions from API)
        max_ltv = _safe_float(supply_info, "maxLTV", "value")
        liq_threshold = _safe_float(supply_info, "liquidationThreshold", "value")
        liq_bonus = _safe_float(supply_info, "liquidationBonus", "value")
        can_be_collateral = supply_info.get("canBeCollateral", False)

        # Convert to percentages for storage
        max_ltv_pct = max_ltv * 100.0 if max_ltv else None
        liq_threshold_pct = liq_threshold * 100.0 if liq_threshold else None

        # Supply cap
        supply_cap_native = _safe_float(supply_info, "supplyCap", "amount", "value")
        supply_cap_usd = _safe_float(supply_info, "supplyCap", "usd")
        supply_cap_reached = supply_info.get("supplyCapReached", False)

        # Total supplied — API returns DecimalValue (native units only)
        total_supplied = _safe_float(supply_info, "total", "value")
        # Estimate USD from supply cap ratio if possible
        total_supplied_usd = None
        if total_supplied is not None and supply_cap_usd and supply_cap_native and supply_cap_native > 0:
            price_est = float(supply_cap_usd) / supply_cap_native
            total_supplied_usd = total_supplied * price_est

        # Available liquidity from borrow side
        avail_liq_usd = _safe_float(borrow_info, "availableLiquidity", "usd")
        avail_liq_native = _safe_float(borrow_info, "availableLiquidity", "amount", "value")

        # Utilization
        util_rate = _safe_float(borrow_info, "utilizationRate", "value")
        util_pct = util_rate * 100.0 if util_rate is not None else None

        # Capacity remaining
        capacity_remaining = None
        is_capacity_capped = supply_cap_native is not None and supply_cap_native > 0
        if is_capacity_capped and total_supplied is not None and supply_cap_native is not None:
            capacity_remaining = max(supply_cap_native - total_supplied, 0.0)

        # E-mode elevated LTVs
        emode_ltv_pct = None
        emode_liq_pct = None
        if emode:
            emode_ltv = _safe_float(emode, "maxLTV", "value")
            emode_liq = _safe_float(emode, "liquidationThreshold", "value")
            emode_ltv_pct = emode_ltv * 100.0 if emode_ltv else None
            emode_liq_pct = emode_liq * 100.0 if emode_liq else None

        # Rate model — from borrow side utilization curve
        rate_model = self._extract_rate_model(
            supply_info=supply_info,
            borrow_info=borrow_info,
        )

        # Receipt token (aToken)
        receipt = ReceiptTokenInfo(
            produces_receipt_token=True,
            receipt_token_symbol=f"a{asset_symbol}",
            is_transferable=True,
            is_composable=True,
            notes="Aave aToken — rebasing, composable across DeFi",
        )

        # Tags
        tags: list[str] = []
        if is_isolated:
            tags.append("isolated")
        if emode:
            emode_label = _safe_str(emode, "label")
            if emode_label:
                tags.append(f"emode:{emode_label}")
        if supply_cap_reached:
            tags.append("supply-cap-reached")

        # Liquidity info
        liquidity = LiquidityInfo(
            available_liquidity=avail_liq_native,
            available_liquidity_usd=avail_liq_usd,
            utilization_rate_pct=util_pct,
        )

        # Reward breakdown
        rewards = [
            RewardBreakdown(
                reward_type=RewardType.NATIVE_YIELD,
                apy_pct=supply_apy_pct,
                is_variable=True,
                notes="Variable supply APY",
            ),
        ]

        return self.build_opportunity(
            asset_id=canonical,
            asset_symbol=asset_symbol,
            chain=chain.value,
            market_id=market_id,
            market_name=f"{market_name} {asset_symbol}",
            side=OpportunitySide.SUPPLY,
            opportunity_type=OpportunityType.LENDING,
            effective_duration=EffectiveDuration.VARIABLE,
            total_apy_pct=supply_apy_pct,
            base_apy_pct=supply_apy_pct,
            reward_breakdown=rewards,
            total_supplied=total_supplied,
            total_supplied_usd=total_supplied_usd,
            capacity_cap=supply_cap_native,
            capacity_remaining=capacity_remaining,
            is_capacity_capped=is_capacity_capped,
            tvl_usd=total_supplied_usd,
            liquidity=liquidity,
            rate_model=rate_model,
            is_collateral_eligible=can_be_collateral,
            as_collateral_max_ltv_pct=max_ltv_pct,
            as_collateral_liquidation_ltv_pct=liq_threshold_pct,
            receipt_token=receipt,
            tags=tags,
            source_url=f"https://app.aave.com/reserve-overview/?underlyingAsset={token_address}",
        )

    def _build_borrow(
        self,
        *,
        canonical: str,
        asset_symbol: str,
        chain: Chain,
        market_id: str,
        market_name: str,
        supply_info: dict,
        borrow_info: dict,
        emode: dict | None,
        is_isolated: bool,
    ) -> MarketOpportunity | None:
        """Build a BORROW-side MarketOpportunity."""
        borrow_apy_raw = _safe_float(borrow_info, "apy", "value")
        if borrow_apy_raw is None:
            return None

        borrow_apy_pct = borrow_apy_raw * 100.0

        # Borrow cap
        borrow_cap_native = _safe_float(borrow_info, "borrowCap", "amount", "value")
        borrow_cap_usd = _safe_float(borrow_info, "borrowCap", "usd")
        borrow_cap_reached = borrow_info.get("borrowCapReached", False)

        # Total borrowed
        total_borrowed = _safe_float(borrow_info, "total", "amount", "value")
        total_borrowed_usd = _safe_float(borrow_info, "total", "usd")

        # Available liquidity
        avail_liq_usd = _safe_float(borrow_info, "availableLiquidity", "usd")
        avail_liq_native = _safe_float(borrow_info, "availableLiquidity", "amount", "value")

        # Utilization
        util_rate = _safe_float(borrow_info, "utilizationRate", "value")
        util_pct = util_rate * 100.0 if util_rate is not None else None

        # Capacity remaining
        capacity_remaining = None
        is_capacity_capped = borrow_cap_native is not None and borrow_cap_native > 0
        if is_capacity_capped and total_borrowed is not None and borrow_cap_native is not None:
            capacity_remaining = max(borrow_cap_native - total_borrowed, 0.0)

        # Rate model
        rate_model = self._extract_rate_model(
            supply_info=supply_info,
            borrow_info=borrow_info,
        )

        # Tags
        tags: list[str] = []
        if is_isolated:
            tags.append("isolated")
        if emode:
            emode_label = _safe_str(emode, "label")
            if emode_label:
                tags.append(f"emode:{emode_label}")
        if borrow_cap_reached:
            tags.append("borrow-cap-reached")

        # Liquidity info
        liquidity = LiquidityInfo(
            available_liquidity=avail_liq_native,
            available_liquidity_usd=avail_liq_usd,
            utilization_rate_pct=util_pct,
        )

        # Reward breakdown (cost, not yield — represented as positive for borrow cost)
        rewards = [
            RewardBreakdown(
                reward_type=RewardType.NATIVE_YIELD,
                apy_pct=borrow_apy_pct,
                is_variable=True,
                notes="Variable borrow APY (cost)",
            ),
        ]

        return self.build_opportunity(
            asset_id=canonical,
            asset_symbol=asset_symbol,
            chain=chain.value,
            market_id=market_id,
            market_name=f"{market_name} {asset_symbol}",
            side=OpportunitySide.BORROW,
            opportunity_type=OpportunityType.LENDING,
            effective_duration=EffectiveDuration.VARIABLE,
            total_apy_pct=borrow_apy_pct,
            base_apy_pct=borrow_apy_pct,
            reward_breakdown=rewards,
            total_borrowed=total_borrowed,
            total_borrowed_usd=total_borrowed_usd,
            capacity_cap=borrow_cap_native,
            capacity_remaining=capacity_remaining,
            is_capacity_capped=is_capacity_capped,
            liquidity=liquidity,
            rate_model=rate_model,
            tags=tags,
        )

    def _extract_rate_model(
        self,
        *,
        supply_info: dict,
        borrow_info: dict,
    ) -> RateModelInfo | None:
        """Extract rate model from current APY and utilization data.

        The API now exposes rate-strategy parameters directly:
        baseVariableBorrowRate, variableRateSlope1, variableRateSlope2,
        and optimalUsageRate.
        """
        supply_rate = _safe_float(supply_info, "apy", "value")
        borrow_rate = _safe_float(borrow_info, "apy", "value")

        if supply_rate is None and borrow_rate is None:
            return None

        # Rate strategy params (exposed as decimal fractions)
        base_rate = _safe_float(borrow_info, "baseVariableBorrowRate", "value")
        slope1 = _safe_float(borrow_info, "variableRateSlope1", "value")
        slope2 = _safe_float(borrow_info, "variableRateSlope2", "value")
        optimal = _safe_float(borrow_info, "optimalUsageRate", "value")

        return RateModelInfo(
            model_type="aave-v3-variable",
            optimal_utilization_pct=optimal * 100.0 if optimal else None,
            base_rate_pct=base_rate * 100.0 if base_rate else None,
            slope1_pct=slope1 * 100.0 if slope1 else None,
            slope2_pct=slope2 * 100.0 if slope2 else None,
            current_supply_rate_pct=supply_rate * 100.0 if supply_rate else None,
            current_borrow_rate_pct=borrow_rate * 100.0 if borrow_rate else None,
        )

    def _build_collateral_matrix(
        self,
        reserves: list[dict],
    ) -> list[CollateralAssetInfo]:
        """Build the collateral matrix: every reserve that can be collateral."""
        matrix: list[CollateralAssetInfo] = []
        for raw in reserves:
            token = raw.get("underlyingToken", {})
            symbol_raw = token.get("symbol", "")
            supply_info = raw.get("supplyInfo", {})
            is_frozen = raw.get("isFrozen", False)
            is_paused = raw.get("isPaused", False)
            iso_config = raw.get("isolationModeConfig") or {}
            is_isolated = not iso_config.get("canBeCollateral", True) if iso_config else False
            emode_list = raw.get("eModeInfo") or []
            emode = emode_list[0] if emode_list else None

            if is_frozen or is_paused:
                continue
            if not supply_info.get("canBeCollateral", False):
                continue

            max_ltv = _safe_float(supply_info, "maxLTV", "value")
            liq_threshold = _safe_float(supply_info, "liquidationThreshold", "value")

            if not max_ltv or max_ltv <= 0:
                continue

            # Total supply = deposit cap proxy
            supply_cap_native = _safe_float(supply_info, "supplyCap", "amount", "value")
            total_supplied = _safe_float(supply_info, "total", "value")
            remaining = None
            if supply_cap_native and total_supplied is not None:
                remaining = max(supply_cap_native - total_supplied, 0.0)

            # E-mode elevated values
            emode_ltv = None
            emode_liq = None
            is_emode = False
            if emode:
                emode_ltv_raw = _safe_float(emode, "maxLTV", "value")
                emode_liq_raw = _safe_float(emode, "liquidationThreshold", "value")
                if emode_ltv_raw and emode_ltv_raw > 0:
                    emode_ltv = emode_ltv_raw * 100.0
                    is_emode = True
                if emode_liq_raw and emode_liq_raw > 0:
                    emode_liq = emode_liq_raw * 100.0

            canonical = self.normalize_symbol(symbol_raw)
            matrix.append(
                CollateralAssetInfo(
                    asset_id=canonical,
                    max_ltv_pct=max_ltv * 100.0,
                    liquidation_ltv_pct=(liq_threshold * 100.0) if liq_threshold else max_ltv * 100.0,
                    deposit_cap=supply_cap_native,
                    current_deposits=total_supplied,
                    remaining_capacity=remaining,
                    is_isolated=is_isolated,
                    is_emode_eligible=is_emode,
                    emode_max_ltv_pct=emode_ltv,
                    emode_liquidation_ltv_pct=emode_liq,
                ),
            )
        return matrix

    # -- ProtocolAdapter abstract methods ------------------------------------

    async def fetch_opportunities(
        self,
        symbols: list[str] | None = None,
        chains: list[Chain] | None = None,
    ) -> list[MarketOpportunity]:
        """Fetch all Aave V3 opportunities across configured chains."""
        # Resolve chain IDs to fetch
        if chains:
            chain_id_set = set()
            for cid, cenum in _CHAIN_ID_TO_ENUM.items():
                if cenum in chains:
                    chain_id_set.add(cid)
            fetch_ids = list(chain_id_set) or self._chain_ids
        else:
            fetch_ids = self._chain_ids

        markets_raw = await self._fetch_markets_raw(chain_ids=fetch_ids)

        all_opportunities: list[MarketOpportunity] = []

        for market in markets_raw:
            market_name = market.get("name", "")
            market_address = market.get("address", "")
            chain_info = market.get("chain", {})
            chain_name = chain_info.get("name", "Ethereum")
            chain_id = chain_info.get("chainId", 1)
            chain = _resolve_chain(chain_name, chain_id)

            reserves_raw = market.get("reserves", [])

            # Build per-market collateral matrix (used for borrow side)
            collateral_matrix = self._build_collateral_matrix(reserves_raw)

            for raw_reserve in reserves_raw:
                try:
                    opps = self._parse_reserve(
                        raw_reserve,
                        market_name=market_name,
                        market_address=market_address,
                        chain=chain,
                    )

                    # Attach collateral matrix to borrow opportunities
                    for opp in opps:
                        if opp.side == OpportunitySide.BORROW and collateral_matrix:
                            # MarketOpportunity is frozen, so rebuild with matrix
                            opp = opp.model_copy(
                                update={"collateral_options": collateral_matrix},
                            )

                        # Filter by symbols if requested
                        if symbols and opp.asset_id not in symbols:
                            continue

                        all_opportunities.append(opp)

                except Exception as exc:
                    token_sym = raw_reserve.get("underlyingToken", {}).get("symbol", "?")
                    log.warning(
                        "aave_v3_reserve_parse_error",
                        market=market_name,
                        chain=chain.value,
                        token=token_sym,
                        error=str(exc),
                    )

        log.info(
            "aave_v3_fetch_done",
            opportunities=len(all_opportunities),
            markets=len(markets_raw),
            chains=fetch_ids,
        )
        return all_opportunities

    async def health_check(self) -> dict[str, Any]:
        """Lightweight probe — run a minimal query against the API."""
        try:
            payload = {
                "query": '{ markets(request: { chainIds: [1] }) { name } }',
            }
            body = await post_json(self._api_url, data=payload)
            has_data = "data" in body or "markets" in body.get("data", {})
            return {
                "status": "ok" if has_data else "degraded",
                "last_success": self._last_success,
                "error": None,
            }
        except Exception as exc:
            return {
                "status": "down",
                "last_success": self._last_success,
                "error": str(exc),
            }
