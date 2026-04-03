"""
Tests for the Aave V3 adapter (:mod:`app.connectors.aave_v3`).

Uses a realistic mock of the Aave official GraphQL API response to verify:
  1. Supply opportunities have correct LTV values
  2. Borrow opportunities have full collateral matrix
  3. Symbol normalization works across chains
  4. E-mode LTVs are correctly populated in collateral matrix
  5. Capacity remaining is calculated correctly
"""
from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.connectors.aave_v3 import AaveV3Adapter
from asset_registry import Chain
from opportunity_schema import OpportunitySide


# ---------------------------------------------------------------------------
# Realistic mock API response
# ---------------------------------------------------------------------------

_MOCK_GRAPHQL_RESPONSE = {
    "data": {
        "markets": [
            {
                "name": "AaveV3Ethereum",
                "address": "0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2",
                "chain": {"name": "Ethereum", "chainId": 1},
                "reserves": [
                    # USDC — standard collateral-eligible asset
                    {
                        "underlyingToken": {
                            "symbol": "USDC",
                            "address": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                            "decimals": 6,
                        },
                        "isFrozen": False,
                        "isPaused": False,
                        "isolationModeConfig": {"canBeCollateral": True},
                        "eModeInfo": [],
                        "supplyInfo": {
                            "apy": {"value": "0.05"},  # 5%
                            "total": {"value": "5000000000"},
                            "maxLTV": {"value": "0.80"},
                            "liquidationThreshold": {"value": "0.85"},
                            "liquidationBonus": {"value": "0.05"},
                            "canBeCollateral": True,
                            "supplyCap": {"usd": "10000000000", "amount": {"value": "10000000000"}},
                            "supplyCapReached": False,
                        },
                        "borrowInfo": {
                            "apy": {"value": "0.07"},  # 7%
                            "total": {"usd": "3000000000", "amount": {"value": "3000000000"}},
                            "borrowCap": {"usd": "8000000000", "amount": {"value": "8000000000"}},
                            "borrowCapReached": False,
                            "availableLiquidity": {"usd": "2000000000", "amount": {"value": "2000000000"}},
                            "utilizationRate": {"value": "0.60"},
                            "borrowingState": "ENABLED",
                            "baseVariableBorrowRate": {"value": "0.0"},
                            "variableRateSlope1": {"value": "0.04"},
                            "variableRateSlope2": {"value": "0.60"},
                            "optimalUsageRate": {"value": "0.90"},
                        },
                    },
                    # wstETH — with e-mode
                    {
                        "underlyingToken": {
                            "symbol": "wstETH",
                            "address": "0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0",
                            "decimals": 18,
                        },
                        "isFrozen": False,
                        "isPaused": False,
                        "isolationModeConfig": {"canBeCollateral": True},
                        "eModeInfo": [
                            {
                                "label": "ETH correlated",
                                "maxLTV": {"value": "0.93"},
                                "liquidationThreshold": {"value": "0.95"},
                                "liquidationPenalty": {"value": "0.01"},
                            },
                        ],
                        "supplyInfo": {
                            "apy": {"value": "0.001"},  # 0.1%
                            "total": {"value": "2500000"},
                            "maxLTV": {"value": "0.71"},
                            "liquidationThreshold": {"value": "0.76"},
                            "liquidationBonus": {"value": "0.06"},
                            "canBeCollateral": True,
                            "supplyCap": {"usd": "10000000000", "amount": {"value": "3000000"}},
                            "supplyCapReached": False,
                        },
                        "borrowInfo": {
                            "apy": {"value": "0.0001"},
                            "total": {"usd": "100000000", "amount": {"value": "30000"}},
                            "borrowCap": {"usd": "500000000", "amount": {"value": "150000"}},
                            "borrowCapReached": False,
                            "availableLiquidity": {"usd": "7900000000", "amount": {"value": "2470000"}},
                            "utilizationRate": {"value": "0.012"},
                            "borrowingState": "ENABLED",
                            "baseVariableBorrowRate": {"value": "0.0"},
                            "variableRateSlope1": {"value": "0.035"},
                            "variableRateSlope2": {"value": "0.80"},
                            "optimalUsageRate": {"value": "0.45"},
                        },
                    },
                    # GHO — isolated, borrowing only (not collateral)
                    {
                        "underlyingToken": {
                            "symbol": "GHO",
                            "address": "0x40d16fc0246ad3160ccc09b8d0d3a2cd28ae6c2f",
                            "decimals": 18,
                        },
                        "isFrozen": False,
                        "isPaused": False,
                        "isolationModeConfig": {"canBeCollateral": False},
                        "eModeInfo": [],
                        "supplyInfo": {
                            "apy": {"value": "0.0"},
                            "total": {"value": "0"},
                            "maxLTV": {"value": "0.0"},
                            "liquidationThreshold": {"value": "0.0"},
                            "liquidationBonus": {"value": "0.0"},
                            "canBeCollateral": False,
                            "supplyCap": {"usd": "0", "amount": {"value": "0"}},
                            "supplyCapReached": False,
                        },
                        "borrowInfo": {
                            "apy": {"value": "0.09"},  # 9%
                            "total": {"usd": "100000000", "amount": {"value": "100000000"}},
                            "borrowCap": {"usd": "500000000", "amount": {"value": "500000000"}},
                            "borrowCapReached": False,
                            "availableLiquidity": {"usd": "400000000", "amount": {"value": "400000000"}},
                            "utilizationRate": {"value": "0.20"},
                            "borrowingState": "ENABLED",
                            "baseVariableBorrowRate": {"value": "0.0"},
                            "variableRateSlope1": {"value": "0.0"},
                            "variableRateSlope2": {"value": "0.0"},
                            "optimalUsageRate": {"value": "0.0"},
                        },
                    },
                    # Frozen reserve — should be skipped
                    {
                        "underlyingToken": {
                            "symbol": "TUSD",
                            "address": "0x0000000000085d4780b73119b644ae5ecd22b376",
                            "decimals": 18,
                        },
                        "isFrozen": True,
                        "isPaused": False,
                        "isolationModeConfig": {"canBeCollateral": True},
                        "eModeInfo": [],
                        "supplyInfo": {
                            "apy": {"value": "0.0"},
                            "total": {"value": "0"},
                            "maxLTV": {"value": "0.0"},
                            "liquidationThreshold": {"value": "0.0"},
                            "liquidationBonus": {"value": "0.0"},
                            "canBeCollateral": False,
                            "supplyCap": {"usd": "0", "amount": {"value": "0"}},
                            "supplyCapReached": False,
                        },
                        "borrowInfo": {
                            "apy": {"value": "0.0"},
                            "total": {"usd": "0", "amount": {"value": "0"}},
                            "borrowCap": {"usd": "0", "amount": {"value": "0"}},
                            "borrowCapReached": False,
                            "availableLiquidity": {"usd": "0", "amount": {"value": "0"}},
                            "utilizationRate": {"value": "0.0"},
                            "borrowingState": "DISABLED",
                            "baseVariableBorrowRate": {"value": "0.0"},
                            "variableRateSlope1": {"value": "0.0"},
                            "variableRateSlope2": {"value": "0.0"},
                            "optimalUsageRate": {"value": "0.0"},
                        },
                    },
                ],
            },
        ],
    },
}

# Response for Arbitrum with USDC.e normalization
_MOCK_ARBITRUM_RESPONSE = {
    "data": {
        "markets": [
            {
                "name": "AaveV3Arbitrum",
                "address": "0x794a61358d6845594f94dc1db02a252b5b4814ad",
                "chain": {"name": "Arbitrum", "chainId": 42161},
                "reserves": [
                    {
                        "underlyingToken": {
                            "symbol": "USDC.e",
                            "address": "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8",
                            "decimals": 6,
                        },
                        "isFrozen": False,
                        "isPaused": False,
                        "isolationModeConfig": {"canBeCollateral": True},
                        "eModeInfo": [],
                        "supplyInfo": {
                            "apy": {"value": "0.04"},
                            "total": {"value": "200000000"},
                            "maxLTV": {"value": "0.80"},
                            "liquidationThreshold": {"value": "0.85"},
                            "liquidationBonus": {"value": "0.05"},
                            "canBeCollateral": True,
                            "supplyCap": {"usd": "500000000", "amount": {"value": "500000000"}},
                            "supplyCapReached": False,
                        },
                        "borrowInfo": {
                            "apy": {"value": "0.06"},
                            "total": {"usd": "120000000", "amount": {"value": "120000000"}},
                            "borrowCap": {"usd": "400000000", "amount": {"value": "400000000"}},
                            "borrowCapReached": False,
                            "availableLiquidity": {"usd": "80000000", "amount": {"value": "80000000"}},
                            "utilizationRate": {"value": "0.60"},
                            "borrowingState": "ENABLED",
                            "baseVariableBorrowRate": {"value": "0.0"},
                            "variableRateSlope1": {"value": "0.04"},
                            "variableRateSlope2": {"value": "0.60"},
                            "optimalUsageRate": {"value": "0.90"},
                        },
                    },
                ],
            },
        ],
    },
}

# Health check response
_MOCK_HEALTH_RESPONSE = {
    "data": {
        "markets": [{"name": "AaveV3Ethereum"}],
    },
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter() -> AaveV3Adapter:
    return AaveV3Adapter()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_supply_opportunity_ltv(adapter: AaveV3Adapter):
    """Supply opportunities have correct LTV values (decimal → percentage)."""
    respx.post(adapter._api_url).mock(
        return_value=Response(200, json=_MOCK_GRAPHQL_RESPONSE),
    )

    opps = await adapter.fetch_opportunities()

    # Find USDC supply
    usdc_supply = [
        o for o in opps
        if o.asset_id == "USDC" and o.side == OpportunitySide.SUPPLY
    ]
    assert len(usdc_supply) == 1
    opp = usdc_supply[0]

    # LTV values: 0.80 → 80.0%, 0.85 → 85.0%
    assert opp.is_collateral_eligible is True
    assert opp.as_collateral_max_ltv_pct == pytest.approx(80.0)
    assert opp.as_collateral_liquidation_ltv_pct == pytest.approx(85.0)

    # APY: 0.05 → 5.0%
    assert opp.total_apy_pct == pytest.approx(5.0)
    assert opp.base_apy_pct == pytest.approx(5.0)

    # Protocol fields
    assert opp.venue == "AAVE_V3"
    assert opp.protocol == "Aave V3"
    assert opp.protocol_slug == "aave-v3"
    assert opp.chain == "ETHEREUM"

    # Receipt token
    assert opp.receipt_token is not None
    assert opp.receipt_token.produces_receipt_token is True
    assert opp.receipt_token.receipt_token_symbol == "aUSDC"
    assert opp.receipt_token.is_transferable is True


@respx.mock
@pytest.mark.asyncio
async def test_borrow_opportunity_collateral_matrix(adapter: AaveV3Adapter):
    """Borrow opportunities have full collateral matrix from all eligible reserves."""
    respx.post(adapter._api_url).mock(
        return_value=Response(200, json=_MOCK_GRAPHQL_RESPONSE),
    )

    opps = await adapter.fetch_opportunities()

    # Find USDC borrow
    usdc_borrow = [
        o for o in opps
        if o.asset_id == "USDC" and o.side == OpportunitySide.BORROW
    ]
    assert len(usdc_borrow) == 1
    opp = usdc_borrow[0]

    # Borrow APY: 0.07 → 7.0%
    assert opp.total_apy_pct == pytest.approx(7.0)

    # Collateral matrix should include USDC and wstETH (both canBeCollateral=True)
    # GHO is not collateral-eligible, TUSD is frozen
    assert opp.collateral_options is not None
    assert len(opp.collateral_options) == 2

    # Check collateral asset IDs
    collateral_ids = {c.asset_id for c in opp.collateral_options}
    assert "USDC" in collateral_ids
    assert "wstETH" in collateral_ids

    # USDC collateral: LTV 80%, liq threshold 85%
    usdc_col = next(c for c in opp.collateral_options if c.asset_id == "USDC")
    assert usdc_col.max_ltv_pct == pytest.approx(80.0)
    assert usdc_col.liquidation_ltv_pct == pytest.approx(85.0)
    assert usdc_col.is_isolated is False


@respx.mock
@pytest.mark.asyncio
async def test_symbol_normalization_across_chains(adapter: AaveV3Adapter):
    """USDC.e on Arbitrum normalizes to canonical USDC."""
    respx.post(adapter._api_url).mock(
        return_value=Response(200, json=_MOCK_ARBITRUM_RESPONSE),
    )

    opps = await adapter.fetch_opportunities(chains=[Chain.ARBITRUM])

    supply_opps = [o for o in opps if o.side == OpportunitySide.SUPPLY]
    assert len(supply_opps) == 1

    opp = supply_opps[0]
    assert opp.asset_id == "USDC"  # normalized from USDC.e
    assert opp.asset_symbol == "USDC.e"  # raw symbol preserved
    assert opp.chain == "ARBITRUM"

    # APY: 0.04 → 4.0%
    assert opp.total_apy_pct == pytest.approx(4.0)


@respx.mock
@pytest.mark.asyncio
async def test_emode_ltvs_in_collateral_matrix(adapter: AaveV3Adapter):
    """E-mode elevated LTV values are correctly populated in collateral matrix."""
    respx.post(adapter._api_url).mock(
        return_value=Response(200, json=_MOCK_GRAPHQL_RESPONSE),
    )

    opps = await adapter.fetch_opportunities()

    # Find any borrow opportunity (they all share the same collateral matrix)
    borrow_opps = [o for o in opps if o.side == OpportunitySide.BORROW]
    assert len(borrow_opps) > 0

    opp = borrow_opps[0]
    assert opp.collateral_options is not None

    # wstETH has e-mode: ltv=0.93 → 93%, liq=0.95 → 95%
    wsteth_col = next(
        (c for c in opp.collateral_options if c.asset_id == "wstETH"),
        None,
    )
    assert wsteth_col is not None
    assert wsteth_col.is_emode_eligible is True
    assert wsteth_col.emode_max_ltv_pct == pytest.approx(93.0)
    assert wsteth_col.emode_liquidation_ltv_pct == pytest.approx(95.0)

    # USDC has no e-mode
    usdc_col = next(c for c in opp.collateral_options if c.asset_id == "USDC")
    assert usdc_col.is_emode_eligible is False
    assert usdc_col.emode_max_ltv_pct is None

    # wstETH supply opportunity should have emode tag
    wsteth_supply = [
        o for o in opps
        if o.asset_id == "wstETH" and o.side == OpportunitySide.SUPPLY
    ]
    assert len(wsteth_supply) == 1
    assert any("emode:" in t for t in wsteth_supply[0].tags)


@respx.mock
@pytest.mark.asyncio
async def test_capacity_remaining_calculation(adapter: AaveV3Adapter):
    """Capacity remaining = cap - current, clamped to 0."""
    respx.post(adapter._api_url).mock(
        return_value=Response(200, json=_MOCK_GRAPHQL_RESPONSE),
    )

    opps = await adapter.fetch_opportunities()

    # USDC supply: cap=10B, supplied=5B → remaining=5B
    usdc_supply = next(
        o for o in opps
        if o.asset_id == "USDC" and o.side == OpportunitySide.SUPPLY
    )
    assert usdc_supply.is_capacity_capped is True
    assert usdc_supply.capacity_cap == pytest.approx(10_000_000_000)
    assert usdc_supply.capacity_remaining == pytest.approx(5_000_000_000)
    assert usdc_supply.total_supplied == pytest.approx(5_000_000_000)
    # tvl_usd is estimated from supply cap USD ratio
    assert usdc_supply.tvl_usd is not None

    # USDC borrow: cap=8B, borrowed=3B → remaining=5B
    usdc_borrow = next(
        o for o in opps
        if o.asset_id == "USDC" and o.side == OpportunitySide.BORROW
    )
    assert usdc_borrow.is_capacity_capped is True
    assert usdc_borrow.capacity_remaining == pytest.approx(5_000_000_000)


@respx.mock
@pytest.mark.asyncio
async def test_frozen_reserves_skipped(adapter: AaveV3Adapter):
    """Frozen reserves produce no opportunities."""
    respx.post(adapter._api_url).mock(
        return_value=Response(200, json=_MOCK_GRAPHQL_RESPONSE),
    )

    opps = await adapter.fetch_opportunities()

    # TUSD is frozen — should not appear
    tusd_opps = [o for o in opps if o.asset_symbol == "TUSD"]
    assert len(tusd_opps) == 0


@respx.mock
@pytest.mark.asyncio
async def test_isolated_asset_tagged(adapter: AaveV3Adapter):
    """Isolated assets are tagged correctly."""
    respx.post(adapter._api_url).mock(
        return_value=Response(200, json=_MOCK_GRAPHQL_RESPONSE),
    )

    opps = await adapter.fetch_opportunities()

    # GHO is isolated — borrow opportunity should be tagged
    gho_borrow = [
        o for o in opps
        if o.asset_id == "GHO" and o.side == OpportunitySide.BORROW
    ]
    assert len(gho_borrow) == 1
    assert "isolated" in gho_borrow[0].tags
    assert gho_borrow[0].total_apy_pct == pytest.approx(9.0)


@respx.mock
@pytest.mark.asyncio
async def test_health_check_ok(adapter: AaveV3Adapter):
    """Health check returns ok when API responds."""
    respx.post(adapter._api_url).mock(
        return_value=Response(200, json=_MOCK_HEALTH_RESPONSE),
    )

    result = await adapter.health_check()
    assert result["status"] == "ok"
    assert result["error"] is None


@respx.mock
@pytest.mark.asyncio
async def test_adapter_properties(adapter: AaveV3Adapter):
    """Adapter has correct static properties."""
    assert adapter.venue == Venue.AAVE_V3
    assert adapter.protocol_name == "Aave V3"
    assert adapter.protocol_slug == "aave-v3"
    assert adapter.refresh_interval_seconds == 300
    assert adapter.requires_api_key is False
    assert adapter.api_key_env_var is None
    assert Chain.ETHEREUM in adapter.supported_chains
    assert Chain.ARBITRUM in adapter.supported_chains


# Need Venue import for the properties test
from asset_registry import Venue
