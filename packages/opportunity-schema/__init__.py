"""
opportunity-schema — Unified yield opportunity data model for Yield OS.

Pure-Python package (+ Pydantic).  No framework dependencies.
"""
from .schema import (
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
    generate_opportunity_id,
)

__all__ = [
    "CollateralAssetInfo",
    "EffectiveDuration",
    "LiquidityInfo",
    "MarketOpportunity",
    "OpportunitySide",
    "OpportunityType",
    "RateModelInfo",
    "ReceiptTokenInfo",
    "RewardBreakdown",
    "RewardType",
    "generate_opportunity_id",
]
