"""
Tests for the Morpho adapter (app.connectors.morpho).

Covers:
  1. Morpho Blue supply opportunity — APY, TVL, no collateral eligibility
  2. Morpho Blue borrow opportunity — single collateral entry with correct LLTV
  3. MetaMorpho vault — VAULT type, OVERNIGHT duration, netApy, ERC-4626 receipt token
  4. Symbol normalization (wstETH, USDC etc.)
  5. AMM LP tokens are skipped on either leg
  6. Chain extraction from API response
"""
from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.connectors.morpho import MorphoAdapter
from asset_registry import Chain
from opportunity_schema import EffectiveDuration, OpportunitySide, OpportunityType


# ---------------------------------------------------------------------------
# Mock responses
# ---------------------------------------------------------------------------

_BLUE_MARKETS_RESPONSE = {
    "data": {
        "markets": {
            "items": [
                # wstETH / WETH market — standard ETH-correlated pair
                {
                    "uniqueKey": "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890ab",
                    "lltv": "860000000000000000",  # 0.86 = 86%
                    "chain": {"id": 1},
                    "loanAsset": {
                        "symbol": "WETH",
                        "address": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
                        "decimals": 18,
                    },
                    "collateralAsset": {
                        "symbol": "wstETH",
                        "address": "0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0",
                        "decimals": 18,
                    },
                    "state": {
                        "supplyAssets": "500000000000000000000",
                        "borrowAssets": "300000000000000000000",
                        "liquidityAssets": "200000000000000000000",
                        "supplyAssetsUsd": "1800000",
                        "borrowAssetsUsd": "1080000",
                        "liquidityAssetsUsd": "720000",
                        "supplyApy": "0.03",    # 3%
                        "borrowApy": "0.05",    # 5%
                        "utilization": "0.60",
                    },
                },
                # USDC / WBTC market on Base
                {
                    "uniqueKey": "0x1111111111111111111111111111111111111111111111111111111111111111",
                    "lltv": "800000000000000000",  # 0.80 = 80%
                    "chain": {"id": 8453},
                    "loanAsset": {
                        "symbol": "USDC",
                        "address": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
                        "decimals": 6,
                    },
                    "collateralAsset": {
                        "symbol": "WBTC",
                        "address": "0x0555e30da8f98308edb960aa94c0db47230d2b9c",
                        "decimals": 8,
                    },
                    "state": {
                        "supplyAssets": "5000000000",
                        "borrowAssets": "3000000000",
                        "liquidityAssets": "2000000000",
                        "supplyAssetsUsd": "5000000",
                        "borrowAssetsUsd": "3000000",
                        "liquidityAssetsUsd": "2000000",
                        "supplyApy": "0.045",   # 4.5%
                        "borrowApy": "0.07",    # 7.0%
                        "utilization": "0.60",
                    },
                },
                # AMM LP collateral — should be skipped
                {
                    "uniqueKey": "0x2222222222222222222222222222222222222222222222222222222222222222",
                    "lltv": "700000000000000000",
                    "chain": {"id": 1},
                    "loanAsset": {
                        "symbol": "USDC",
                        "address": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                        "decimals": 6,
                    },
                    "collateralAsset": {
                        "symbol": "UNI-V2 WETH/USDC LP",
                        "address": "0xb4e16d0168e52d35cacd2c6185b44281ec28c9dc",
                        "decimals": 18,
                    },
                    "state": {
                        "supplyAssets": "1000000",
                        "borrowAssets": "500000",
                        "liquidityAssets": "500000",
                        "supplyAssetsUsd": "1000",
                        "borrowAssetsUsd": "500",
                        "liquidityAssetsUsd": "500",
                        "supplyApy": "0.04",
                        "borrowApy": "0.06",
                        "utilization": "0.50",
                    },
                },
            ],
        },
        "vaults": {"items": []},
    },
}

_VAULTS_RESPONSE = {
    "data": {
        "markets": {"items": []},
        "vaults": {
            "items": [
                {
                    "address": "0xbeef000000000000000000000000000000000001",
                    "name": "Gauntlet USDC Prime",
                    "symbol": "gauntletUSDCprime",
                    "chain": {"id": 1},
                    "asset": {
                        "symbol": "USDC",
                        "address": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                        "decimals": 6,
                    },
                    "state": {
                        "totalAssets": "500000000000",
                        "totalAssetsUsd": "500000000",
                        "apy": "0.065",      # 6.5% gross
                        "netApy": "0.06",    # 6.0% net after fee
                        "fee": "0.10",       # 10% performance fee
                    },
                },
                {
                    "address": "0xbeef000000000000000000000000000000000002",
                    "name": "Steakhouse ETH",
                    "symbol": "steakETH",
                    "chain": {"id": 1},
                    "asset": {
                        "symbol": "WETH",
                        "address": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
                        "decimals": 18,
                    },
                    "state": {
                        "totalAssets": "10000000000000000000000",
                        "totalAssetsUsd": "36000000",
                        "apy": "0.025",
                        "netApy": "0.022",
                        "fee": "0.15",
                    },
                },
            ],
        },
    },
}

# Combined response used for most tests
_COMBINED_RESPONSE = {
    "data": {
        "markets": _BLUE_MARKETS_RESPONSE["data"]["markets"],
        "vaults": _VAULTS_RESPONSE["data"]["vaults"],
    },
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter() -> MorphoAdapter:
    return MorphoAdapter()


# ---------------------------------------------------------------------------
# Morpho Blue tests
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_blue_supply_opportunity(adapter: MorphoAdapter):
    """Morpho Blue supply: correct APY, TVL, NOT collateral-eligible."""
    respx.post(adapter._api_url).mock(return_value=Response(200, json=_COMBINED_RESPONSE))

    opps = await adapter.fetch_opportunities(chains=[Chain.ETHEREUM])
    supply_opps = [o for o in opps if o.side == OpportunitySide.SUPPLY and o.asset_id == "WETH"]

    # wstETH/WETH market supply
    weth_supply = next(
        (o for o in supply_opps if o.opportunity_type == OpportunityType.LENDING),
        None,
    )
    assert weth_supply is not None

    # APY: 0.03 → 3%
    assert weth_supply.total_apy_pct == pytest.approx(3.0)
    assert weth_supply.base_apy_pct == pytest.approx(3.0)
    assert weth_supply.tvl_usd == pytest.approx(1_800_000)
    assert weth_supply.chain == "ETHEREUM"
    assert weth_supply.venue == "MORPHO"
    assert weth_supply.protocol_slug == "morpho-blue"

    # Morpho Blue supply is NOT collateral-eligible
    assert weth_supply.is_collateral_eligible is False
    assert weth_supply.collateral_options is None

    # Liquidity info
    assert weth_supply.liquidity.available_liquidity_usd == pytest.approx(720_000)
    assert weth_supply.liquidity.utilization_rate_pct == pytest.approx(60.0)


@respx.mock
@pytest.mark.asyncio
async def test_blue_borrow_single_collateral(adapter: MorphoAdapter):
    """Morpho Blue borrow: exactly ONE collateral entry with correct LLTV."""
    respx.post(adapter._api_url).mock(return_value=Response(200, json=_COMBINED_RESPONSE))

    opps = await adapter.fetch_opportunities(chains=[Chain.ETHEREUM])
    borrow_opps = [o for o in opps if o.side == OpportunitySide.BORROW and o.asset_id == "WETH"]

    weth_borrow = next(
        (o for o in borrow_opps if o.opportunity_type == OpportunityType.LENDING),
        None,
    )
    assert weth_borrow is not None

    # Borrow APY: 0.05 → 5%
    assert weth_borrow.total_apy_pct == pytest.approx(5.0)

    # Morpho Blue: exactly ONE collateral option
    assert weth_borrow.collateral_options is not None
    assert len(weth_borrow.collateral_options) == 1

    col = weth_borrow.collateral_options[0]
    assert col.asset_id == "wstETH"

    # LLTV 0.86 → 86% liquidation, 86 * 0.95 = 81.7% max
    assert col.liquidation_ltv_pct == pytest.approx(86.0)
    assert col.max_ltv_pct == pytest.approx(81.7)
    assert col.is_isolated is True


@respx.mock
@pytest.mark.asyncio
async def test_blue_borrow_lltv_conversion(adapter: MorphoAdapter):
    """LLTV 1e18 string correctly converts to percentage."""
    respx.post(adapter._api_url).mock(return_value=Response(200, json=_COMBINED_RESPONSE))

    opps = await adapter.fetch_opportunities(chains=[Chain.BASE])
    base_borrow = [
        o for o in opps
        if o.side == OpportunitySide.BORROW and o.asset_id == "USDC" and o.chain == "BASE"
    ]
    assert len(base_borrow) == 1

    col = base_borrow[0].collateral_options[0]
    # LLTV 0.80 → 80% liquidation, 80 * 0.95 = 76% max
    assert col.liquidation_ltv_pct == pytest.approx(80.0)
    assert col.max_ltv_pct == pytest.approx(76.0)
    assert col.asset_id == "WBTC"


# ---------------------------------------------------------------------------
# MetaMorpho vault tests
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_vault_opportunity_type(adapter: MorphoAdapter):
    """MetaMorpho vaults produce VAULT type, OVERNIGHT duration, SUPPLY side."""
    respx.post(adapter._api_url).mock(return_value=Response(200, json=_COMBINED_RESPONSE))

    opps = await adapter.fetch_opportunities()
    vault_opps = [o for o in opps if o.opportunity_type == OpportunityType.VAULT]

    assert len(vault_opps) == 2

    usdc_vault = next(o for o in vault_opps if o.asset_id == "USDC")
    assert usdc_vault.side == OpportunitySide.SUPPLY
    assert usdc_vault.effective_duration == EffectiveDuration.OVERNIGHT
    assert usdc_vault.venue == "MORPHO"
    assert usdc_vault.protocol_slug == "metamorpho"


@respx.mock
@pytest.mark.asyncio
async def test_vault_uses_net_apy(adapter: MorphoAdapter):
    """Vault APY uses netApy (after fee), not gross apy."""
    respx.post(adapter._api_url).mock(return_value=Response(200, json=_COMBINED_RESPONSE))

    opps = await adapter.fetch_opportunities()
    vault_opps = [o for o in opps if o.opportunity_type == OpportunityType.VAULT]

    usdc_vault = next(o for o in vault_opps if o.asset_id == "USDC")
    # netApy = 0.06 → 6.0% (NOT gross 6.5%)
    assert usdc_vault.total_apy_pct == pytest.approx(6.0)

    weth_vault = next(o for o in vault_opps if o.asset_id == "WETH")
    # netApy = 0.022 → 2.2%
    assert weth_vault.total_apy_pct == pytest.approx(2.2)


@respx.mock
@pytest.mark.asyncio
async def test_vault_receipt_token(adapter: MorphoAdapter):
    """MetaMorpho vault shares are ERC-4626, transferable, composable."""
    respx.post(adapter._api_url).mock(return_value=Response(200, json=_COMBINED_RESPONSE))

    opps = await adapter.fetch_opportunities()
    vault_opps = [o for o in opps if o.opportunity_type == OpportunityType.VAULT]

    usdc_vault = next(o for o in vault_opps if o.asset_id == "USDC")
    r = usdc_vault.receipt_token
    assert r is not None
    assert r.produces_receipt_token is True
    assert r.receipt_token_symbol == "gauntletUSDCprime"
    assert r.is_transferable is True
    assert r.is_composable is True

    # Tagged as metamorpho
    assert "metamorpho" in usdc_vault.tags


@respx.mock
@pytest.mark.asyncio
async def test_vault_tvl_and_source_url(adapter: MorphoAdapter):
    """Vault TVL and source URL are correctly populated."""
    respx.post(adapter._api_url).mock(return_value=Response(200, json=_COMBINED_RESPONSE))

    opps = await adapter.fetch_opportunities()
    usdc_vault = next(o for o in opps if o.opportunity_type == OpportunityType.VAULT and o.asset_id == "USDC")

    assert usdc_vault.tvl_usd == pytest.approx(500_000_000)
    assert usdc_vault.source_url == "https://app.morpho.org/vault?vault=0xbeef000000000000000000000000000000000001"


# ---------------------------------------------------------------------------
# Normalization and filtering tests
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_amm_lp_collateral_skipped(adapter: MorphoAdapter):
    """Markets with AMM LP collateral tokens produce no opportunities."""
    respx.post(adapter._api_url).mock(return_value=Response(200, json=_COMBINED_RESPONSE))

    opps = await adapter.fetch_opportunities()

    # The UNI-V2 LP market should be completely absent
    lp_opps = [o for o in opps if "UNI-V2" in (o.asset_symbol or "")]
    assert len(lp_opps) == 0


@respx.mock
@pytest.mark.asyncio
async def test_symbol_normalization(adapter: MorphoAdapter):
    """wstETH normalizes to canonical wstETH."""
    respx.post(adapter._api_url).mock(return_value=Response(200, json=_COMBINED_RESPONSE))

    opps = await adapter.fetch_opportunities()
    borrow_opps = [o for o in opps if o.side == OpportunitySide.BORROW and o.chain == "ETHEREUM"]

    # Collateral in the wstETH/WETH borrow market should be canonical wstETH
    weth_borrow = next(o for o in borrow_opps if o.asset_id == "WETH")
    col = weth_borrow.collateral_options[0]
    assert col.asset_id == "wstETH"

    # Raw symbol preserved in asset_symbol
    assert weth_borrow.asset_symbol == "WETH"


@respx.mock
@pytest.mark.asyncio
async def test_chain_filtering(adapter: MorphoAdapter):
    """Chain filtering restricts results to requested chains."""
    respx.post(adapter._api_url).mock(return_value=Response(200, json=_COMBINED_RESPONSE))

    eth_opps = await adapter.fetch_opportunities(chains=[Chain.ETHEREUM])
    base_opps = await adapter.fetch_opportunities(chains=[Chain.BASE])

    # All eth opps should have ETHEREUM chain
    assert all(o.chain == "ETHEREUM" for o in eth_opps if o.opportunity_type == OpportunityType.LENDING)
    # Base results exist for USDC/WBTC market
    base_lending = [o for o in base_opps if o.opportunity_type == OpportunityType.LENDING]
    assert len(base_lending) > 0
    assert all(o.chain == "BASE" for o in base_lending)


@respx.mock
@pytest.mark.asyncio
async def test_adapter_properties(adapter: MorphoAdapter):
    """Adapter static properties are correct."""
    from asset_registry import Venue
    assert adapter.venue == Venue.MORPHO
    assert adapter.protocol_name == "Morpho"
    assert adapter.refresh_interval_seconds == 300
    assert adapter.requires_api_key is False
    assert Chain.ETHEREUM in adapter.supported_chains
    assert Chain.BASE in adapter.supported_chains


@respx.mock
@pytest.mark.asyncio
async def test_health_check(adapter: MorphoAdapter):
    """Health check returns ok when API responds."""
    respx.post(adapter._api_url).mock(
        return_value=Response(200, json={"data": {"markets": {"items": [{"uniqueKey": "0x1"}]}}})
    )
    result = await adapter.health_check()
    assert result["status"] == "ok"
    assert result["error"] is None
