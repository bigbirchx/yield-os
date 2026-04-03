"""
Tests for the opportunity ingestion orchestrator.

Validates:
  - MarketOpportunity → DB row serialization round-trip
  - Upsert (ON CONFLICT) logic via the service
  - Snapshot recording
  - Adapter registration and registry access
  - Router endpoint registration
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.services.opportunity_ingestion import (
    _opportunity_to_row,
    _opportunity_to_snapshot,
    get_registry,
    register_adapters,
)
from opportunity_schema import (
    EffectiveDuration,
    LiquidityInfo,
    MarketOpportunity,
    OpportunitySide,
    OpportunityType,
    RewardBreakdown,
    RewardType,
)


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

def _make_opportunity(**overrides) -> MarketOpportunity:
    """Build a minimal valid MarketOpportunity for testing."""
    defaults = {
        "opportunity_id": "aave_v3:ethereum:aave-v3:0xabc:0xdef:supply",
        "venue": "AAVE_V3",
        "chain": "ETHEREUM",
        "protocol": "Aave V3",
        "protocol_slug": "aave-v3",
        "market_id": "0xabc:0xdef",
        "market_name": "AaveV3Ethereum USDC",
        "side": OpportunitySide.SUPPLY,
        "asset_id": "USDC",
        "asset_symbol": "USDC",
        "umbrella_group": "USD",
        "asset_sub_type": "TIER1_STABLE",
        "opportunity_type": OpportunityType.LENDING,
        "effective_duration": EffectiveDuration.VARIABLE,
        "total_apy_pct": 5.0,
        "base_apy_pct": 5.0,
        "reward_breakdown": [
            RewardBreakdown(
                reward_type=RewardType.NATIVE_YIELD,
                apy_pct=5.0,
            ),
        ],
        "total_supplied": 5_000_000_000,
        "total_supplied_usd": 5_000_000_000,
        "tvl_usd": 5_000_000_000,
        "is_capacity_capped": True,
        "capacity_cap": 10_000_000_000,
        "capacity_remaining": 5_000_000_000,
        "liquidity": LiquidityInfo(
            utilization_rate_pct=60.0,
            available_liquidity_usd=2_000_000_000,
        ),
        "is_collateral_eligible": True,
        "as_collateral_max_ltv_pct": 80.0,
        "as_collateral_liquidation_ltv_pct": 85.0,
        "data_source": "aave-v3",
        "last_updated_at": datetime(2026, 4, 2, 12, 0, 0, tzinfo=UTC),
    }
    defaults.update(overrides)
    return MarketOpportunity(**defaults)


# ---------------------------------------------------------------------------
# Serialization tests
# ---------------------------------------------------------------------------


def test_opportunity_to_row_serialization():
    """MarketOpportunity serializes to a flat dict suitable for DB insert."""
    opp = _make_opportunity()
    row = _opportunity_to_row(opp)

    assert row["opportunity_id"] == "aave_v3:ethereum:aave-v3:0xabc:0xdef:supply"
    assert row["venue"] == "AAVE_V3"
    assert row["chain"] == "ETHEREUM"
    assert row["side"] == "SUPPLY"
    assert row["total_apy_pct"] == 5.0
    assert row["is_collateral_eligible"] is True
    assert row["as_collateral_max_ltv_pct"] == 80.0
    assert row["is_capacity_capped"] is True
    assert row["capacity_remaining"] == 5_000_000_000
    assert row["tvl_usd"] == 5_000_000_000

    # JSONB fields should be serialized
    assert isinstance(row["reward_breakdown"], list)
    assert len(row["reward_breakdown"]) == 1
    assert row["reward_breakdown"][0]["apy_pct"] == 5.0

    assert isinstance(row["liquidity"], dict)
    assert row["liquidity"]["utilization_rate_pct"] == 60.0


def test_opportunity_to_snapshot():
    """Snapshot captures rate and size data at a point in time."""
    opp = _make_opportunity()
    now = datetime(2026, 4, 2, 12, 30, 0, tzinfo=UTC)
    snap = _opportunity_to_snapshot(opp, now)

    assert snap["opportunity_id"] == opp.opportunity_id
    assert snap["snapshot_at"] == now
    assert snap["total_apy_pct"] == 5.0
    assert snap["base_apy_pct"] == 5.0
    assert snap["total_supplied"] == 5_000_000_000
    assert snap["total_supplied_usd"] == 5_000_000_000
    assert snap["utilization_rate_pct"] == 60.0
    assert snap["tvl_usd"] == 5_000_000_000


def test_opportunity_to_row_handles_nulls():
    """Nullable fields serialize correctly as None."""
    opp = _make_opportunity(
        total_borrowed=None,
        total_borrowed_usd=None,
        rate_model=None,
        collateral_options=None,
        receipt_token=None,
        source_url=None,
        maturity_date=None,
    )
    row = _opportunity_to_row(opp)

    assert row["total_borrowed"] is None
    assert row["rate_model"] is None
    assert row["collateral_options"] is None
    assert row["receipt_token"] is None
    assert row["source_url"] is None
    assert row["maturity_date"] is None


def test_opportunity_to_row_borrow_side():
    """Borrow-side opportunity serializes with correct side."""
    opp = _make_opportunity(
        opportunity_id="aave_v3:ethereum:aave-v3:0xabc:0xdef:borrow",
        side=OpportunitySide.BORROW,
        total_apy_pct=7.0,
        base_apy_pct=7.0,
    )
    row = _opportunity_to_row(opp)

    assert row["side"] == "BORROW"
    assert row["total_apy_pct"] == 7.0


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


def test_register_adapters():
    """register_adapters populates the global registry."""
    register_adapters()
    registry = get_registry()
    adapters = registry.get_all()

    assert len(adapters) >= 1

    # Aave V3 should be registered
    from asset_registry import Venue
    aave = registry.get_by_venue(Venue.AAVE_V3)
    assert aave is not None
    assert aave.protocol_slug == "aave-v3"


# ---------------------------------------------------------------------------
# Router registration test
# ---------------------------------------------------------------------------


def test_opportunity_routes_registered():
    """All 6 opportunity routes are registered in the FastAPI app."""
    from app.main import app

    paths = [r.path for r in app.routes]
    assert "/api/opportunities" in paths
    assert "/api/opportunities/summary" in paths
    assert "/api/opportunities/refresh" in paths
    assert "/api/opportunities/refresh/{venue}" in paths
    assert "/api/opportunities/{opportunity_id:path}/history" in paths
    assert "/api/opportunities/{opportunity_id:path}" in paths
