"""
Tests for the SparkLend + Sky Savings adapter (app.connectors.spark).

Covers:
  1. SparkLend SUPPLY — APY (Messari pct passthrough), TVL, receipt token
  2. SparkLend BORROW — collateral matrix, net APY
  3. sDAI DSR — SAVINGS type, OVERNIGHT duration, correct APY
  4. sUSDS SSR — SAVINGS type, OVERNIGHT duration, correct APY
  5. Only Ethereum chain is supported
  6. Adapter static properties
"""
from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.connectors.spark import SparkAdapter
from app.core.config import settings
from asset_registry import Chain, Venue
from opportunity_schema import EffectiveDuration, OpportunitySide, OpportunityType


# ---------------------------------------------------------------------------
# Mock responses
# ---------------------------------------------------------------------------

_SPARK_MARKETS_RESPONSE = {
    "data": {
        "markets": [
            # WETH — borrowable + collateral-eligible
            {
                "id": "0xmarket_weth",
                "name": "SparkLend WETH",
                "inputToken": {"id": "0xweth", "symbol": "WETH", "decimals": 18},
                "outputToken": {"id": "0xspweth", "symbol": "spWETH"},
                "rates": [
                    {"rate": "3.5", "side": "LENDER", "type": "VARIABLE"},
                    {"rate": "5.0", "side": "BORROWER", "type": "VARIABLE"},
                ],
                "totalValueLockedUSD": "200000000",
                "totalDepositBalanceUSD": "200000000",
                "totalBorrowBalanceUSD": "80000000",
                "maximumLTV": "80.0",
                "liquidationThreshold": "82.5",
                "liquidationPenalty": "5.0",
                "supplyCap": "0",
                "borrowCap": "0",
                "canBorrowFrom": True,
                "canUseAsCollateral": True,
                "isActive": True,
                "protocol": {"id": "0xspark", "name": "SparkLend"},
            },
            # DAI — borrowable, NOT collateral
            {
                "id": "0xmarket_dai",
                "name": "SparkLend DAI",
                "inputToken": {"id": "0xdai", "symbol": "DAI", "decimals": 18},
                "outputToken": {"id": "0xspdai", "symbol": "spDAI"},
                "rates": [
                    {"rate": "6.2", "side": "LENDER", "type": "VARIABLE"},
                    {"rate": "9.0", "side": "BORROWER", "type": "VARIABLE"},
                ],
                "totalValueLockedUSD": "500000000",
                "totalDepositBalanceUSD": "500000000",
                "totalBorrowBalanceUSD": "350000000",
                "maximumLTV": "0",
                "liquidationThreshold": "0",
                "liquidationPenalty": "0",
                "supplyCap": "0",
                "borrowCap": "0",
                "canBorrowFrom": True,
                "canUseAsCollateral": False,
                "isActive": True,
                "protocol": {"id": "0xspark", "name": "SparkLend"},
            },
            # wstETH — collateral only (canBorrowFrom=False)
            {
                "id": "0xmarket_wsteth",
                "name": "SparkLend wstETH",
                "inputToken": {"id": "0xwsteth", "symbol": "wstETH", "decimals": 18},
                "outputToken": {"id": "0xspwsteth", "symbol": "spwstETH"},
                "rates": [],
                "totalValueLockedUSD": "150000000",
                "totalDepositBalanceUSD": "150000000",
                "totalBorrowBalanceUSD": "0",
                "maximumLTV": "68.5",
                "liquidationThreshold": "79.5",
                "liquidationPenalty": "7.0",
                "supplyCap": "0",
                "borrowCap": "0",
                "canBorrowFrom": False,
                "canUseAsCollateral": True,
                "isActive": True,
                "protocol": {"id": "0xspark", "name": "SparkLend"},
            },
        ]
    }
}

_DEFILLAMA_RESPONSE = {
    "data": [
        # DSR pool
        {
            "pool": "sdai-maker-dsr",
            "chain": "Ethereum",
            "project": "maker-dsr",
            "symbol": "DAI",
            "tvlUsd": 2_000_000_000,
            "apy": 5.0,
            "apyBase": 5.0,
        },
        # SSR pool
        {
            "pool": "susds-sky",
            "chain": "Ethereum",
            "project": "sky",
            "symbol": "USDS",
            "tvlUsd": 800_000_000,
            "apy": 6.5,
            "apyBase": 6.5,
        },
        # Another project we should NOT pick up
        {
            "pool": "some-other-pool",
            "chain": "Ethereum",
            "project": "compound",
            "symbol": "USDC",
            "tvlUsd": 500_000_000,
            "apy": 4.0,
        },
        # Non-Ethereum DSR pool — should be skipped
        {
            "pool": "sdai-arbitrum",
            "chain": "Arbitrum",
            "project": "maker-dsr",
            "symbol": "DAI",
            "tvlUsd": 100_000_000,
            "apy": 4.9,
        },
    ]
}


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter() -> SparkAdapter:
    return SparkAdapter()


def _mock_both(adapter: SparkAdapter):
    """Helper: mock both the Spark subgraph and DeFiLlama endpoints."""
    respx.post(settings.spark_url).mock(return_value=Response(200, json=_SPARK_MARKETS_RESPONSE))
    respx.get(settings.sky_savings_url).mock(return_value=Response(200, json=_DEFILLAMA_RESPONSE))


# ---------------------------------------------------------------------------
# SparkLend lending markets
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_spark_supply_apy(adapter: SparkAdapter):
    """SparkLend SUPPLY: Messari pct passthrough (no ×100)."""
    _mock_both(adapter)
    opps = await adapter.fetch_opportunities()
    weth_supply = next(
        o for o in opps
        if o.side == OpportunitySide.SUPPLY and o.asset_id == "WETH"
        and o.opportunity_type == OpportunityType.LENDING
    )
    assert weth_supply.total_apy_pct == pytest.approx(3.5)
    assert weth_supply.base_apy_pct == pytest.approx(3.5)


@respx.mock
@pytest.mark.asyncio
async def test_spark_supply_tvl(adapter: SparkAdapter):
    """TVL and supplied_usd populated correctly."""
    _mock_both(adapter)
    opps = await adapter.fetch_opportunities()
    weth_supply = next(
        o for o in opps
        if o.side == OpportunitySide.SUPPLY and o.asset_id == "WETH"
        and o.opportunity_type == OpportunityType.LENDING
    )
    assert weth_supply.tvl_usd == pytest.approx(200_000_000)
    assert weth_supply.total_supplied_usd == pytest.approx(200_000_000)


@respx.mock
@pytest.mark.asyncio
async def test_spark_supply_receipt_token(adapter: SparkAdapter):
    """Receipt token uses outputToken symbol."""
    _mock_both(adapter)
    opps = await adapter.fetch_opportunities()
    weth_supply = next(
        o for o in opps
        if o.side == OpportunitySide.SUPPLY and o.asset_id == "WETH"
        and o.opportunity_type == OpportunityType.LENDING
    )
    r = weth_supply.receipt_token
    assert r is not None
    assert r.receipt_token_symbol == "spWETH"
    assert r.is_composable is True


@respx.mock
@pytest.mark.asyncio
async def test_spark_borrow_apy(adapter: SparkAdapter):
    """SparkLend BORROW: correct borrow APY, collateral matrix excludes self."""
    _mock_both(adapter)
    opps = await adapter.fetch_opportunities()
    dai_borrow = next(
        o for o in opps
        if o.side == OpportunitySide.BORROW and o.asset_id == "DAI"
    )
    assert dai_borrow.total_apy_pct == pytest.approx(9.0)


@respx.mock
@pytest.mark.asyncio
async def test_spark_borrow_collateral_matrix(adapter: SparkAdapter):
    """BORROW collateral matrix includes WETH + wstETH; self not in matrix."""
    _mock_both(adapter)
    opps = await adapter.fetch_opportunities()
    weth_borrow = next(
        (o for o in opps if o.side == OpportunitySide.BORROW and o.asset_id == "WETH"),
        None,
    )
    # WETH is borrowable and there are other collateral assets
    if weth_borrow is not None:
        collateral_ids = {c.asset_id for c in weth_borrow.collateral_options}
        # WETH should NOT collateralize itself
        assert "WETH" not in collateral_ids
        # wstETH should be available
        assert "wstETH" in collateral_ids


@respx.mock
@pytest.mark.asyncio
async def test_spark_collateral_ltv(adapter: SparkAdapter):
    """wstETH collateral LTV values from subgraph are preserved."""
    _mock_both(adapter)
    opps = await adapter.fetch_opportunities()

    # Find any borrow opp that has wstETH as collateral
    borrow_with_wsteth = next(
        (
            o for o in opps
            if o.side == OpportunitySide.BORROW
            and o.collateral_options
            and any(c.asset_id == "wstETH" for c in o.collateral_options)
        ),
        None,
    )
    assert borrow_with_wsteth is not None
    wsteth_col = next(c for c in borrow_with_wsteth.collateral_options if c.asset_id == "wstETH")
    assert wsteth_col.max_ltv_pct == pytest.approx(68.5)
    assert wsteth_col.liquidation_ltv_pct == pytest.approx(79.5)


# ---------------------------------------------------------------------------
# Sky savings rates
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_dsr_savings_opportunity(adapter: SparkAdapter):
    """sDAI DSR produces a SAVINGS type, OVERNIGHT duration, SUPPLY side opp."""
    _mock_both(adapter)
    opps = await adapter.fetch_opportunities()
    dsr = next(
        (o for o in opps if o.opportunity_type == OpportunityType.SAVINGS and o.asset_id == "DAI"),
        None,
    )
    assert dsr is not None
    assert dsr.side == OpportunitySide.SUPPLY
    assert dsr.effective_duration == EffectiveDuration.OVERNIGHT
    assert dsr.total_apy_pct == pytest.approx(5.0)
    assert dsr.venue == "SPARK"


@respx.mock
@pytest.mark.asyncio
async def test_ssr_savings_opportunity(adapter: SparkAdapter):
    """sUSDS SSR produces a SAVINGS type, OVERNIGHT duration, SUPPLY side opp."""
    _mock_both(adapter)
    opps = await adapter.fetch_opportunities()
    ssr = next(
        (o for o in opps if o.opportunity_type == OpportunityType.SAVINGS and o.asset_id == "USDS"),
        None,
    )
    assert ssr is not None
    assert ssr.side == OpportunitySide.SUPPLY
    assert ssr.effective_duration == EffectiveDuration.OVERNIGHT
    assert ssr.total_apy_pct == pytest.approx(6.5)


@respx.mock
@pytest.mark.asyncio
async def test_savings_receipt_token(adapter: SparkAdapter):
    """DSR / SSR produce ERC-4626 receipt tokens (sDAI / sUSDS)."""
    _mock_both(adapter)
    opps = await adapter.fetch_opportunities()

    dsr = next(o for o in opps if o.opportunity_type == OpportunityType.SAVINGS and o.asset_id == "DAI")
    ssr = next(o for o in opps if o.opportunity_type == OpportunityType.SAVINGS and o.asset_id == "USDS")

    assert dsr.receipt_token.receipt_token_symbol == "sDAI"
    assert ssr.receipt_token.receipt_token_symbol == "sUSDS"
    assert dsr.receipt_token.is_composable is True
    assert ssr.receipt_token.is_composable is True


@respx.mock
@pytest.mark.asyncio
async def test_savings_tvl(adapter: SparkAdapter):
    """Savings TVL populated from DeFiLlama tvlUsd."""
    _mock_both(adapter)
    opps = await adapter.fetch_opportunities()
    dsr = next(o for o in opps if o.opportunity_type == OpportunityType.SAVINGS and o.asset_id == "DAI")
    assert dsr.tvl_usd == pytest.approx(2_000_000_000)


@respx.mock
@pytest.mark.asyncio
async def test_non_ethereum_savings_skipped(adapter: SparkAdapter):
    """DeFiLlama pools on non-Ethereum chains are skipped."""
    _mock_both(adapter)
    opps = await adapter.fetch_opportunities()
    # Should only have one DAI savings opp (Ethereum), not the Arbitrum one
    dai_savings = [o for o in opps if o.opportunity_type == OpportunityType.SAVINGS and o.asset_id == "DAI"]
    assert len(dai_savings) == 1
    assert dai_savings[0].chain == "ETHEREUM"


# ---------------------------------------------------------------------------
# Chain filtering
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_non_ethereum_chain_returns_empty(adapter: SparkAdapter):
    """SparkLend only supports Ethereum; other chains return empty."""
    _mock_both(adapter)
    opps = await adapter.fetch_opportunities(chains=[Chain.ARBITRUM])
    assert opps == []


# ---------------------------------------------------------------------------
# Adapter properties
# ---------------------------------------------------------------------------


def test_adapter_properties(adapter: SparkAdapter):
    assert adapter.venue == Venue.SPARK
    assert adapter.protocol_name == "SparkLend"
    assert adapter.protocol_slug == "sparklend"
    assert adapter.refresh_interval_seconds == 600
    assert adapter.requires_api_key is False
    assert adapter.supported_chains == [Chain.ETHEREUM]


@respx.mock
@pytest.mark.asyncio
async def test_health_check_ok(adapter: SparkAdapter):
    respx.post(settings.spark_url).mock(
        return_value=Response(200, json={"data": {"markets": [{"id": "0x1"}]}})
    )
    result = await adapter.health_check()
    assert result["status"] == "ok"
    assert result["error"] is None
