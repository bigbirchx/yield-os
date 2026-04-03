"""
Unified yield opportunity schema for Yield OS.

Every protocol adapter — DeFi lending, CeFi earn, staking, funding rates,
basis trades, Pendle, vaults — emits :class:`MarketOpportunity` instances.
This gives the cockpit a single data model to sort, filter, compare, and
route across all yield sources.

Pure-Python package (+ Pydantic).  No framework dependencies.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class OpportunitySide(str, Enum):
    """Whether the position is supplying or borrowing."""

    SUPPLY = "SUPPLY"
    BORROW = "BORROW"


class OpportunityType(str, Enum):
    """Structural classification of the yield source."""

    LENDING = "LENDING"
    VAULT = "VAULT"
    STAKING = "STAKING"
    RESTAKING = "RESTAKING"
    SAVINGS = "SAVINGS"
    FUNDING_RATE = "FUNDING_RATE"
    BASIS_TRADE = "BASIS_TRADE"
    CEX_EARN = "CEX_EARN"
    PENDLE_PT = "PENDLE_PT"
    PENDLE_YT = "PENDLE_YT"
    AMM_LP = "AMM_LP"
    DEX_LP_LENDING = "DEX_LP_LENDING"


class EffectiveDuration(str, Enum):
    """How easily the position can be entered and exited."""

    OVERNIGHT = "OVERNIGHT"
    VARIABLE = "VARIABLE"
    FIXED_TERM = "FIXED_TERM"
    PERPETUAL = "PERPETUAL"
    EPOCH_BASED = "EPOCH_BASED"


class RewardType(str, Enum):
    """Classification of a yield component."""

    NATIVE_YIELD = "NATIVE_YIELD"
    TOKEN_INCENTIVE = "TOKEN_INCENTIVE"
    POINTS = "POINTS"
    FEE_SHARE = "FEE_SHARE"
    BOOSTED = "BOOSTED"


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class RewardBreakdown(BaseModel):
    """One component of total yield."""

    reward_type: RewardType
    token_id: str | None = None
    token_name: str | None = None
    apy_pct: float
    is_variable: bool = True
    notes: str | None = None

    model_config = {"frozen": True}


class CollateralAssetInfo(BaseModel):
    """One collateral option available for a borrow-side opportunity."""

    asset_id: str
    max_ltv_pct: float
    liquidation_ltv_pct: float
    deposit_cap: float | None = None
    current_deposits: float | None = None
    remaining_capacity: float | None = None
    is_isolated: bool = False
    is_emode_eligible: bool = False
    emode_max_ltv_pct: float | None = None
    emode_liquidation_ltv_pct: float | None = None

    model_config = {"frozen": True}


class LiquidityInfo(BaseModel):
    """Exit-risk and liquidity profile for an opportunity."""

    has_lockup: bool = False
    lockup_days: float | None = None
    has_withdrawal_queue: bool = False
    current_queue_length_days: float | None = None
    available_liquidity: float | None = None
    available_liquidity_usd: float | None = None
    utilization_rate_pct: float | None = None
    notes: str | None = None

    model_config = {"frozen": True}


class RateModelInfo(BaseModel):
    """Interest rate model parameters for impact estimation."""

    model_type: str | None = None
    optimal_utilization_pct: float | None = None
    base_rate_pct: float | None = None
    slope1_pct: float | None = None
    slope2_pct: float | None = None
    current_supply_rate_pct: float | None = None
    current_borrow_rate_pct: float | None = None

    model_config = {"frozen": True}


class ReceiptTokenInfo(BaseModel):
    """Whether the opportunity produces a transferable/composable receipt token."""

    produces_receipt_token: bool = False
    receipt_token_id: str | None = None
    receipt_token_symbol: str | None = None
    is_transferable: bool = False
    is_composable: bool = False
    composable_venues: list[str] | None = None
    notes: str | None = None

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Main model
# ---------------------------------------------------------------------------


class MarketOpportunity(BaseModel):
    """
    Canonical representation of a single yield opportunity.

    Every protocol adapter normalises its native data into this shape before
    it enters the cockpit's storage and ranking layers.  The schema is
    intentionally wide: optional fields are ``None`` when a given adapter
    doesn't have the data, but the type system guarantees that two
    opportunities from completely different domains (e.g. an Aave USDC
    supply market and a Binance BTC funding rate) can be compared
    side-by-side without translation.
    """

    # ── Identity ─────────────────────────────────────────────────────────
    opportunity_id: str
    venue: str
    chain: str
    protocol: str
    protocol_slug: str
    market_id: str
    market_name: str | None = None
    side: OpportunitySide
    asset_id: str
    asset_symbol: str
    umbrella_group: str
    asset_sub_type: str
    opportunity_type: OpportunityType
    effective_duration: EffectiveDuration
    maturity_date: datetime | None = None
    days_to_maturity: float | None = None

    # ── Yield ────────────────────────────────────────────────────────────
    total_apy_pct: float
    base_apy_pct: float
    reward_breakdown: list[RewardBreakdown] = []

    # ── Size and capacity ────────────────────────────────────────────────
    total_supplied: float | None = None
    total_supplied_usd: float | None = None
    total_borrowed: float | None = None
    total_borrowed_usd: float | None = None
    capacity_cap: float | None = None
    capacity_remaining: float | None = None
    is_capacity_capped: bool = False
    tvl_usd: float | None = None

    # ── Liquidity and exit risk ──────────────────────────────────────────
    liquidity: LiquidityInfo = LiquidityInfo()

    # ── Rate model ───────────────────────────────────────────────────────
    rate_model: RateModelInfo | None = None

    # ── Collateral (supply side: can this deposit be used as collateral?) ─
    is_collateral_eligible: bool = False
    as_collateral_max_ltv_pct: float | None = None
    as_collateral_liquidation_ltv_pct: float | None = None

    # ── Collateral matrix (borrow side: what can back this borrow?) ──────
    collateral_options: list[CollateralAssetInfo] | None = None

    # ── Receipt token ────────────────────────────────────────────────────
    receipt_token: ReceiptTokenInfo | None = None

    # ── Filtering tags ───────────────────────────────────────────────────
    is_amm_lp: bool = False
    is_pendle: bool = False
    pendle_type: str | None = None
    tags: list[str] = []

    # ── Metadata ─────────────────────────────────────────────────────────
    data_source: str
    last_updated_at: datetime
    data_freshness_seconds: int = 0
    source_url: str | None = None

    # ── Historical (populated separately) ────────────────────────────────
    historical_rates_7d: list[dict] | None = None
    historical_rates_30d: list[dict] | None = None

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def generate_opportunity_id(
    venue: str,
    chain: str,
    protocol: str,
    market_id: str,
    side: str | OpportunitySide,
) -> str:
    """Build a deterministic, human-readable opportunity ID.

    Format: ``{venue}:{chain}:{protocol}:{market_id}:{side}``

    All components are lowercased for consistency.

    >>> generate_opportunity_id("AAVE_V3", "ETHEREUM", "aave-v3", "0xabc…", "SUPPLY")
    'aave_v3:ethereum:aave-v3:0xabc…:supply'
    """
    side_val = side.value if isinstance(side, OpportunitySide) else side
    return ":".join(
        part.lower()
        for part in (venue, chain, protocol, market_id, side_val)
    )
