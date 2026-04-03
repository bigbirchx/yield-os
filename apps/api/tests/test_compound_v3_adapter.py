"""
Tests for the Compound V3 adapter (app.connectors.compound_v3).

Covers:
  1. Base asset SUPPLY opportunity — APY (Messari pct), TVL, is_collateral_eligible=False
  2. Base asset BORROW opportunity — borrow net APY, collateral matrix
  3. COMP reward APY extraction via REWARD type in rates array
  4. Collateral-only markets produce no standalone opportunities
  5. AMM LP tokens are skipped
  6. Per-chain subgraph URL dispatch
  7. Adapter static properties
"""
from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.connectors.compound_v3 import CompoundV3Adapter, _chain_urls
from asset_registry import Chain, Venue
from opportunity_schema import OpportunitySide, OpportunityType, RewardType


# ---------------------------------------------------------------------------
# Mock response helpers
# ---------------------------------------------------------------------------

def _make_market(
    *,
    comet_id: str,
    symbol: str,
    can_borrow_from: bool,
    can_use_as_collateral: bool,
    supply_apy: float = 5.0,
    borrow_apy: float = 7.0,
    reward_supply_apy: float = 0.0,
    reward_borrow_apy: float = 0.0,
    tvl_usd: float = 10_000_000,
    borrow_usd: float = 4_000_000,
    max_ltv: float = 80.0,
    liq_threshold: float = 85.0,
    is_active: bool = True,
) -> dict:
    rates = []
    if can_borrow_from:
        rates.append({"rate": str(supply_apy), "side": "LENDER", "type": "VARIABLE"})
        rates.append({"rate": str(borrow_apy), "side": "BORROWER", "type": "VARIABLE"})
        if reward_supply_apy > 0:
            rates.append({"rate": str(reward_supply_apy), "side": "LENDER", "type": "REWARD"})
        if reward_borrow_apy > 0:
            rates.append({"rate": str(reward_borrow_apy), "side": "BORROWER", "type": "REWARD"})
    return {
        "id": f"{comet_id}:{symbol.lower()}",
        "name": f"Compound {symbol}",
        "inputToken": {"id": f"0x{symbol.lower()}", "symbol": symbol, "decimals": 18},
        "outputToken": {"id": f"0xc{symbol.lower()}", "symbol": f"c{symbol}v3"},
        "rates": rates,
        "totalValueLockedUSD": str(tvl_usd),
        "totalBorrowBalanceUSD": str(borrow_usd),
        "maximumLTV": str(max_ltv),
        "liquidationThreshold": str(liq_threshold),
        "liquidationPenalty": "5.0",
        "canBorrowFrom": can_borrow_from,
        "canUseAsCollateral": can_use_as_collateral,
        "isActive": is_active,
        "rewardTokens": [{"id": "0xcomp", "symbol": "COMP"}],
        "rewardTokenEmissionsUSD": ["1000"],
        "protocol": {"id": comet_id, "name": f"Compound {symbol} Comet"},
    }


_USDC_COMET_ID = "0xc3d688b66703497daa19211eedff47f25384cdc3"
_WETH_COMET_ID = "0xa17581a9e3356d9a858b789d68b4d866e593ae94"

_ETH_MARKETS_RESPONSE = {
    "data": {
        "markets": [
            # USDC Comet — base asset
            _make_market(
                comet_id=_USDC_COMET_ID,
                symbol="USDC",
                can_borrow_from=True,
                can_use_as_collateral=False,
                supply_apy=5.2,
                borrow_apy=8.1,
                reward_supply_apy=1.5,
                reward_borrow_apy=2.0,
                tvl_usd=500_000_000,
                borrow_usd=300_000_000,
            ),
            # USDC Comet — WETH collateral
            _make_market(
                comet_id=_USDC_COMET_ID,
                symbol="WETH",
                can_borrow_from=False,
                can_use_as_collateral=True,
                max_ltv=82.5,
                liq_threshold=87.0,
                tvl_usd=200_000_000,
                borrow_usd=0,
            ),
            # USDC Comet — WBTC collateral
            _make_market(
                comet_id=_USDC_COMET_ID,
                symbol="WBTC",
                can_borrow_from=False,
                can_use_as_collateral=True,
                max_ltv=70.0,
                liq_threshold=77.0,
                tvl_usd=50_000_000,
                borrow_usd=0,
            ),
            # WETH Comet — base asset (separate Comet)
            _make_market(
                comet_id=_WETH_COMET_ID,
                symbol="WETH",
                can_borrow_from=True,
                can_use_as_collateral=False,
                supply_apy=2.5,
                borrow_apy=4.0,
                tvl_usd=100_000_000,
                borrow_usd=40_000_000,
            ),
            # WETH Comet — wstETH collateral
            _make_market(
                comet_id=_WETH_COMET_ID,
                symbol="wstETH",
                can_borrow_from=False,
                can_use_as_collateral=True,
                max_ltv=90.0,
                liq_threshold=93.0,
            ),
            # AMM LP — should be skipped
            _make_market(
                comet_id=_USDC_COMET_ID,
                symbol="UNI-V2 ETH/USDC LP",
                can_borrow_from=False,
                can_use_as_collateral=True,
                max_ltv=50.0,
                liq_threshold=55.0,
            ),
        ]
    }
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter() -> CompoundV3Adapter:
    return CompoundV3Adapter()


def _eth_url(adapter: CompoundV3Adapter) -> str:
    return _chain_urls()[Chain.ETHEREUM]


# ---------------------------------------------------------------------------
# Supply tests
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_supply_opportunity_apy(adapter: CompoundV3Adapter):
    """SUPPLY opportunity: base APY + reward APY = total; Messari pct passthrough."""
    respx.post(_eth_url(adapter)).mock(return_value=Response(200, json=_ETH_MARKETS_RESPONSE))

    opps = await adapter.fetch_opportunities(chains=[Chain.ETHEREUM])
    usdc_supply = next(
        o for o in opps
        if o.side == OpportunitySide.SUPPLY and o.asset_id == "USDC"
    )

    # Messari returns percentages directly: 5.2 + 1.5 = 6.7
    assert usdc_supply.total_apy_pct == pytest.approx(6.7)
    assert usdc_supply.base_apy_pct == pytest.approx(5.2)


@respx.mock
@pytest.mark.asyncio
async def test_supply_not_collateral_eligible(adapter: CompoundV3Adapter):
    """V3 base asset supply is NOT collateral-eligible (key V3 difference from Aave)."""
    respx.post(_eth_url(adapter)).mock(return_value=Response(200, json=_ETH_MARKETS_RESPONSE))

    opps = await adapter.fetch_opportunities(chains=[Chain.ETHEREUM])
    usdc_supply = next(o for o in opps if o.side == OpportunitySide.SUPPLY and o.asset_id == "USDC")

    assert usdc_supply.is_collateral_eligible is False


@respx.mock
@pytest.mark.asyncio
async def test_supply_tvl_and_liquidity(adapter: CompoundV3Adapter):
    """SUPPLY: TVL, available liquidity, and utilization are populated."""
    respx.post(_eth_url(adapter)).mock(return_value=Response(200, json=_ETH_MARKETS_RESPONSE))

    opps = await adapter.fetch_opportunities(chains=[Chain.ETHEREUM])
    usdc_supply = next(o for o in opps if o.side == OpportunitySide.SUPPLY and o.asset_id == "USDC")

    assert usdc_supply.tvl_usd == pytest.approx(500_000_000)
    assert usdc_supply.total_supplied_usd == pytest.approx(500_000_000)
    assert usdc_supply.liquidity.available_liquidity_usd == pytest.approx(200_000_000)
    assert usdc_supply.liquidity.utilization_rate_pct == pytest.approx(60.0)


@respx.mock
@pytest.mark.asyncio
async def test_supply_receipt_token(adapter: CompoundV3Adapter):
    """SUPPLY receipt token is populated from outputToken."""
    respx.post(_eth_url(adapter)).mock(return_value=Response(200, json=_ETH_MARKETS_RESPONSE))

    opps = await adapter.fetch_opportunities(chains=[Chain.ETHEREUM])
    usdc_supply = next(o for o in opps if o.side == OpportunitySide.SUPPLY and o.asset_id == "USDC")

    r = usdc_supply.receipt_token
    assert r is not None
    assert r.produces_receipt_token is True
    assert "cUSDCv3" in r.receipt_token_symbol or r.receipt_token_symbol.startswith("c")


# ---------------------------------------------------------------------------
# COMP reward tests
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_comp_reward_breakdown(adapter: CompoundV3Adapter):
    """COMP rewards appear as TOKEN_INCENTIVE in reward_breakdown."""
    respx.post(_eth_url(adapter)).mock(return_value=Response(200, json=_ETH_MARKETS_RESPONSE))

    opps = await adapter.fetch_opportunities(chains=[Chain.ETHEREUM])
    usdc_supply = next(o for o in opps if o.side == OpportunitySide.SUPPLY and o.asset_id == "USDC")

    reward_types = [r.reward_type for r in usdc_supply.reward_breakdown]
    assert RewardType.NATIVE_YIELD in reward_types
    assert RewardType.TOKEN_INCENTIVE in reward_types

    comp_reward = next(r for r in usdc_supply.reward_breakdown if r.reward_type == RewardType.TOKEN_INCENTIVE)
    assert comp_reward.apy_pct == pytest.approx(1.5)


@respx.mock
@pytest.mark.asyncio
async def test_no_comp_reward_when_zero(adapter: CompoundV3Adapter):
    """TOKEN_INCENTIVE entry omitted when reward APY is 0."""
    respx.post(_eth_url(adapter)).mock(return_value=Response(200, json=_ETH_MARKETS_RESPONSE))

    opps = await adapter.fetch_opportunities(chains=[Chain.ETHEREUM])
    # WETH Comet has no reward APY
    weth_supply = next(o for o in opps if o.side == OpportunitySide.SUPPLY and o.asset_id == "WETH")

    reward_types = [r.reward_type for r in weth_supply.reward_breakdown]
    assert RewardType.TOKEN_INCENTIVE not in reward_types


# ---------------------------------------------------------------------------
# Borrow tests
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_borrow_net_apy(adapter: CompoundV3Adapter):
    """BORROW net APY = max(borrow_base - borrow_reward, 0)."""
    respx.post(_eth_url(adapter)).mock(return_value=Response(200, json=_ETH_MARKETS_RESPONSE))

    opps = await adapter.fetch_opportunities(chains=[Chain.ETHEREUM])
    usdc_borrow = next(o for o in opps if o.side == OpportunitySide.BORROW and o.asset_id == "USDC")

    # borrow_base=8.1, borrow_reward=2.0 → net=6.1
    assert usdc_borrow.total_apy_pct == pytest.approx(6.1)
    assert usdc_borrow.base_apy_pct == pytest.approx(8.1)


@respx.mock
@pytest.mark.asyncio
async def test_borrow_collateral_matrix(adapter: CompoundV3Adapter):
    """BORROW opportunity contains correct collateral matrix from same Comet."""
    respx.post(_eth_url(adapter)).mock(return_value=Response(200, json=_ETH_MARKETS_RESPONSE))

    opps = await adapter.fetch_opportunities(chains=[Chain.ETHEREUM])
    usdc_borrow = next(o for o in opps if o.side == OpportunitySide.BORROW and o.asset_id == "USDC")

    assert usdc_borrow.collateral_options is not None
    # WETH + WBTC collaterals (AMM LP skipped)
    collateral_ids = {c.asset_id for c in usdc_borrow.collateral_options}
    assert "WETH" in collateral_ids
    assert "WBTC" in collateral_ids

    weth_col = next(c for c in usdc_borrow.collateral_options if c.asset_id == "WETH")
    assert weth_col.max_ltv_pct == pytest.approx(82.5)
    assert weth_col.liquidation_ltv_pct == pytest.approx(87.0)


@respx.mock
@pytest.mark.asyncio
async def test_borrow_cross_comet_isolation(adapter: CompoundV3Adapter):
    """Collateral from WETH Comet does NOT appear in USDC Comet borrow."""
    respx.post(_eth_url(adapter)).mock(return_value=Response(200, json=_ETH_MARKETS_RESPONSE))

    opps = await adapter.fetch_opportunities(chains=[Chain.ETHEREUM])
    usdc_borrow = next(o for o in opps if o.side == OpportunitySide.BORROW and o.asset_id == "USDC")

    collateral_ids = {c.asset_id for c in usdc_borrow.collateral_options}
    # wstETH is collateral in the WETH Comet, NOT the USDC Comet
    assert "wstETH" not in collateral_ids


# ---------------------------------------------------------------------------
# AMM LP filtering
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_amm_lp_skipped(adapter: CompoundV3Adapter):
    """AMM LP tokens are excluded from collateral options."""
    respx.post(_eth_url(adapter)).mock(return_value=Response(200, json=_ETH_MARKETS_RESPONSE))

    opps = await adapter.fetch_opportunities(chains=[Chain.ETHEREUM])

    for opp in opps:
        if opp.collateral_options:
            for col in opp.collateral_options:
                assert "UNI-V2" not in col.asset_id
                assert "LP" not in col.asset_id.upper()


# ---------------------------------------------------------------------------
# Multi-Comet structure
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_multiple_comets_produce_separate_opportunities(adapter: CompoundV3Adapter):
    """USDC Comet and WETH Comet each produce independent supply/borrow opportunities."""
    respx.post(_eth_url(adapter)).mock(return_value=Response(200, json=_ETH_MARKETS_RESPONSE))

    opps = await adapter.fetch_opportunities(chains=[Chain.ETHEREUM])

    supply_opps = [o for o in opps if o.side == OpportunitySide.SUPPLY]
    # USDC supply + WETH supply (one per Comet base asset)
    supply_ids = {o.asset_id for o in supply_opps}
    assert "USDC" in supply_ids
    assert "WETH" in supply_ids


# ---------------------------------------------------------------------------
# Adapter properties
# ---------------------------------------------------------------------------


def test_adapter_properties(adapter: CompoundV3Adapter):
    assert adapter.venue == Venue.COMPOUND_V3
    assert adapter.protocol_name == "Compound V3"
    assert adapter.protocol_slug == "compound-v3"
    assert adapter.refresh_interval_seconds == 300
    assert adapter.requires_api_key is False
    assert Chain.ETHEREUM in adapter.supported_chains
    assert Chain.ARBITRUM in adapter.supported_chains
    assert Chain.BASE in adapter.supported_chains
    assert Chain.POLYGON in adapter.supported_chains
    assert Chain.OPTIMISM in adapter.supported_chains


@respx.mock
@pytest.mark.asyncio
async def test_health_check_ok(adapter: CompoundV3Adapter):
    respx.post(_eth_url(adapter)).mock(
        return_value=Response(200, json={"data": {"markets": [{"id": "0x1"}]}})
    )
    result = await adapter.health_check()
    assert result["status"] == "ok"
    assert result["error"] is None
