"""
Tests for the Euler V2 adapter (app.connectors.euler_v2).

Covers:
  1. SUPPLY opportunity — APY (decimal→pct), TVL, receipt token
  2. BORROW opportunity — created only when collateral LTVs present
  3. Collateral LTV conversion (decimal → pct)
  4. AMM LP tokens skipped in collateral list
  5. Chain filtering (client-side guard)
  6. Adapter static properties
"""
from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.connectors.euler_v2 import EulerV2Adapter
from app.core.config import settings
from asset_registry import Chain, Venue
from opportunity_schema import OpportunitySide, OpportunityType, RewardType


# ---------------------------------------------------------------------------
# Mock response
# ---------------------------------------------------------------------------

_VAULTS_RESPONSE = {
    "data": {
        "vaults": [
            # USDC vault on Ethereum with collateral
            {
                "id": "0xvaultusdc",
                "address": "0xvaultusdc",
                "name": "Euler USDC Prime",
                "symbol": "eUSDC",
                "chainId": 1,
                "asset": {"id": "0xusdc", "symbol": "USDC", "decimals": 6},
                "supplyAPY": "0.065",   # 6.5%
                "borrowAPY": "0.095",   # 9.5%
                "totalSupplyAssetsUSD": "50000000",
                "totalBorrowAssetsUSD": "30000000",
                "supplyCap": "0",       # uncapped
                "borrowCap": "0",       # uncapped
                "collateralLTVs": [
                    {
                        "collateral": {
                            "id": "0xweth",
                            "address": "0xweth",
                            "symbol": "WETH",
                            "totalSupplyAssetsUSD": "100000000",
                            "maximumLTV": "0.8",
                            "liquidationLTV": "0.85",
                        },
                        "borrowLTV": "0.80",
                        "liquidationLTV": "0.85",
                    },
                    {
                        "collateral": {
                            "id": "0xwbtc",
                            "address": "0xwbtc",
                            "symbol": "WBTC",
                            "totalSupplyAssetsUSD": "30000000",
                            "maximumLTV": "0.70",
                            "liquidationLTV": "0.77",
                        },
                        "borrowLTV": "0.70",
                        "liquidationLTV": "0.77",
                    },
                    # AMM LP — should be skipped
                    {
                        "collateral": {
                            "id": "0xlp",
                            "address": "0xlp",
                            "symbol": "UNI-V2 WETH/USDC LP",
                            "totalSupplyAssetsUSD": "1000",
                            "maximumLTV": "0.5",
                            "liquidationLTV": "0.55",
                        },
                        "borrowLTV": "0.50",
                        "liquidationLTV": "0.55",
                    },
                ],
            },
            # WETH vault on Arbitrum — no collateral configured → no borrow opp
            {
                "id": "0xvaultweth",
                "address": "0xvaultweth",
                "name": "Euler WETH Arbitrum",
                "symbol": "eWETH",
                "chainId": 42161,
                "asset": {"id": "0xweth", "symbol": "WETH", "decimals": 18},
                "supplyAPY": "0.025",   # 2.5%
                "borrowAPY": "0.04",    # 4.0%
                "totalSupplyAssetsUSD": "5000000",
                "totalBorrowAssetsUSD": "1000000",
                "supplyCap": "0",
                "borrowCap": "0",
                "collateralLTVs": [],
            },
            # Base chain vault
            {
                "id": "0xvaultbase",
                "address": "0xvaultbase",
                "name": "Euler USDC Base",
                "symbol": "eUSDCbase",
                "chainId": 8453,
                "asset": {"id": "0xusdcbase", "symbol": "USDC", "decimals": 6},
                "supplyAPY": "0.04",
                "borrowAPY": "0.06",
                "totalSupplyAssetsUSD": "2000000",
                "totalBorrowAssetsUSD": "800000",
                "supplyCap": "0",
                "borrowCap": "0",
                "collateralLTVs": [],
            },
        ]
    }
}


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter() -> EulerV2Adapter:
    return EulerV2Adapter()


# ---------------------------------------------------------------------------
# Supply tests
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_supply_apy_decimal_to_pct(adapter: EulerV2Adapter):
    """Supply APY decimal fractions are multiplied ×100."""
    respx.post(settings.euler_v2_url).mock(return_value=Response(200, json=_VAULTS_RESPONSE))

    opps = await adapter.fetch_opportunities(chains=[Chain.ETHEREUM])
    usdc_supply = next(
        o for o in opps
        if o.side == OpportunitySide.SUPPLY and o.asset_id == "USDC"
    )

    assert usdc_supply.total_apy_pct == pytest.approx(6.5)
    assert usdc_supply.base_apy_pct == pytest.approx(6.5)


@respx.mock
@pytest.mark.asyncio
async def test_supply_tvl(adapter: EulerV2Adapter):
    """TVL and total_supplied_usd are populated from API."""
    respx.post(settings.euler_v2_url).mock(return_value=Response(200, json=_VAULTS_RESPONSE))

    opps = await adapter.fetch_opportunities(chains=[Chain.ETHEREUM])
    usdc_supply = next(o for o in opps if o.side == OpportunitySide.SUPPLY and o.asset_id == "USDC")

    assert usdc_supply.tvl_usd == pytest.approx(50_000_000)
    assert usdc_supply.total_supplied_usd == pytest.approx(50_000_000)


@respx.mock
@pytest.mark.asyncio
async def test_supply_receipt_token(adapter: EulerV2Adapter):
    """ERC-4626 receipt token is populated with vault symbol."""
    respx.post(settings.euler_v2_url).mock(return_value=Response(200, json=_VAULTS_RESPONSE))

    opps = await adapter.fetch_opportunities(chains=[Chain.ETHEREUM])
    usdc_supply = next(o for o in opps if o.side == OpportunitySide.SUPPLY and o.asset_id == "USDC")

    r = usdc_supply.receipt_token
    assert r is not None
    assert r.produces_receipt_token is True
    assert r.receipt_token_symbol == "eUSDC"
    assert r.is_transferable is True
    assert r.is_composable is True


@respx.mock
@pytest.mark.asyncio
async def test_supply_reward_breakdown(adapter: EulerV2Adapter):
    """SUPPLY reward breakdown has NATIVE_YIELD entry."""
    respx.post(settings.euler_v2_url).mock(return_value=Response(200, json=_VAULTS_RESPONSE))

    opps = await adapter.fetch_opportunities(chains=[Chain.ETHEREUM])
    usdc_supply = next(o for o in opps if o.side == OpportunitySide.SUPPLY and o.asset_id == "USDC")

    assert len(usdc_supply.reward_breakdown) == 1
    assert usdc_supply.reward_breakdown[0].reward_type == RewardType.NATIVE_YIELD


# ---------------------------------------------------------------------------
# Borrow tests
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_borrow_opportunity_created_with_collateral(adapter: EulerV2Adapter):
    """BORROW opportunity created when collateralLTVs is non-empty."""
    respx.post(settings.euler_v2_url).mock(return_value=Response(200, json=_VAULTS_RESPONSE))

    opps = await adapter.fetch_opportunities(chains=[Chain.ETHEREUM])
    borrow_opps = [o for o in opps if o.side == OpportunitySide.BORROW]

    # USDC vault has collateral → should have a borrow opp
    assert any(o.asset_id == "USDC" for o in borrow_opps)


@respx.mock
@pytest.mark.asyncio
async def test_no_borrow_without_collateral(adapter: EulerV2Adapter):
    """BORROW opportunity NOT created when collateralLTVs is empty."""
    respx.post(settings.euler_v2_url).mock(return_value=Response(200, json=_VAULTS_RESPONSE))

    opps = await adapter.fetch_opportunities(chains=[Chain.ARBITRUM])
    borrow_opps = [o for o in opps if o.side == OpportunitySide.BORROW]

    # WETH Arbitrum vault has no collateral → no borrow
    assert len(borrow_opps) == 0


@respx.mock
@pytest.mark.asyncio
async def test_borrow_collateral_ltv_conversion(adapter: EulerV2Adapter):
    """Collateral LTVs converted from decimal fractions to percentages."""
    respx.post(settings.euler_v2_url).mock(return_value=Response(200, json=_VAULTS_RESPONSE))

    opps = await adapter.fetch_opportunities(chains=[Chain.ETHEREUM])
    usdc_borrow = next(o for o in opps if o.side == OpportunitySide.BORROW and o.asset_id == "USDC")

    weth_col = next(c for c in usdc_borrow.collateral_options if c.asset_id == "WETH")
    assert weth_col.max_ltv_pct == pytest.approx(80.0)
    assert weth_col.liquidation_ltv_pct == pytest.approx(85.0)

    wbtc_col = next(c for c in usdc_borrow.collateral_options if c.asset_id == "WBTC")
    assert wbtc_col.max_ltv_pct == pytest.approx(70.0)
    assert wbtc_col.liquidation_ltv_pct == pytest.approx(77.0)


@respx.mock
@pytest.mark.asyncio
async def test_amm_lp_excluded_from_collateral(adapter: EulerV2Adapter):
    """AMM LP tokens excluded from collateral options."""
    respx.post(settings.euler_v2_url).mock(return_value=Response(200, json=_VAULTS_RESPONSE))

    opps = await adapter.fetch_opportunities(chains=[Chain.ETHEREUM])
    usdc_borrow = next(o for o in opps if o.side == OpportunitySide.BORROW and o.asset_id == "USDC")

    collateral_ids = {c.asset_id for c in usdc_borrow.collateral_options}
    assert not any("UNI-V2" in cid for cid in collateral_ids)
    # Only WETH and WBTC
    assert collateral_ids == {"WETH", "WBTC"}


# ---------------------------------------------------------------------------
# Chain filtering
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_chain_filtering_ethereum(adapter: EulerV2Adapter):
    """Chain filter restricts to Ethereum vaults."""
    respx.post(settings.euler_v2_url).mock(return_value=Response(200, json=_VAULTS_RESPONSE))

    opps = await adapter.fetch_opportunities(chains=[Chain.ETHEREUM])
    assert all(o.chain == "ETHEREUM" for o in opps)


@respx.mock
@pytest.mark.asyncio
async def test_chain_filtering_arbitrum(adapter: EulerV2Adapter):
    """Chain filter restricts to Arbitrum vaults."""
    respx.post(settings.euler_v2_url).mock(return_value=Response(200, json=_VAULTS_RESPONSE))

    opps = await adapter.fetch_opportunities(chains=[Chain.ARBITRUM])
    assert all(o.chain == "ARBITRUM" for o in opps)
    assert any(o.asset_id == "WETH" for o in opps)


# ---------------------------------------------------------------------------
# Adapter properties
# ---------------------------------------------------------------------------


def test_adapter_properties(adapter: EulerV2Adapter):
    assert adapter.venue == Venue.EULER_V2
    assert adapter.protocol_name == "Euler V2"
    assert adapter.protocol_slug == "euler-v2"
    assert adapter.refresh_interval_seconds == 600
    assert adapter.requires_api_key is False
    assert Chain.ETHEREUM in adapter.supported_chains
    assert Chain.ARBITRUM in adapter.supported_chains
    assert Chain.BASE in adapter.supported_chains


@respx.mock
@pytest.mark.asyncio
async def test_health_check_ok(adapter: EulerV2Adapter):
    respx.post(settings.euler_v2_url).mock(
        return_value=Response(200, json={"data": {"vaults": [{"id": "0x1"}]}})
    )
    result = await adapter.health_check()
    assert result["status"] == "ok"
    assert result["error"] is None
