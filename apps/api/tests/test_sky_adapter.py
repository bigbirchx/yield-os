"""
Tests for the Sky savings-rate adapter (app.connectors.sky).

Covers:
  1. sDAI DSR — SAVINGS type, OVERNIGHT duration, correct APY
  2. sUSDS SSR — SAVINGS type, OVERNIGHT duration, correct APY
  3. Receipt tokens (sDAI / sUSDS) — composable, ERC-4626
  4. TVL populated from DeFiLlama
  5. Only Ethereum pools accepted; other chains skipped
  6. Venue is SKY, not SPARK (separate from SparkAdapter)
  7. Chain guard — non-Ethereum chains return empty list
  8. Symbol filter respected
  9. Health check — ok when DSR pool found, degraded when absent
  10. Adapter static properties
"""
from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.connectors.sky import SkyAdapter
from app.core.config import settings
from asset_registry import Chain, Venue
from opportunity_schema import EffectiveDuration, OpportunitySide, OpportunityType


# ---------------------------------------------------------------------------
# Mock DeFiLlama response
# ---------------------------------------------------------------------------

_DEFILLAMA_RESPONSE = {
    "data": [
        # DSR pool — Ethereum
        {
            "pool": "sdai-maker-dsr",
            "chain": "Ethereum",
            "project": "maker-dsr",
            "symbol": "DAI",
            "tvlUsd": 2_500_000_000,
            "apy": 5.25,
            "apyBase": 5.25,
        },
        # SSR pool — Ethereum
        {
            "pool": "susds-sky",
            "chain": "Ethereum",
            "project": "sky",
            "symbol": "USDS",
            "tvlUsd": 900_000_000,
            "apy": 6.75,
            "apyBase": 6.75,
        },
        # Non-Ethereum DSR pool — must be skipped
        {
            "pool": "sdai-arbitrum",
            "chain": "Arbitrum",
            "project": "maker-dsr",
            "symbol": "DAI",
            "tvlUsd": 50_000_000,
            "apy": 5.10,
        },
        # Unrelated project — must be skipped
        {
            "pool": "some-compound-pool",
            "chain": "Ethereum",
            "project": "compound",
            "symbol": "USDC",
            "tvlUsd": 400_000_000,
            "apy": 4.0,
        },
    ]
}

_DEFILLAMA_NO_DSR = {
    "data": [
        {
            "pool": "susds-sky",
            "chain": "Ethereum",
            "project": "sky",
            "symbol": "USDS",
            "tvlUsd": 900_000_000,
            "apy": 6.75,
        },
        {
            "pool": "some-other",
            "chain": "Ethereum",
            "project": "compound",
            "symbol": "USDC",
            "tvlUsd": 100_000_000,
            "apy": 3.0,
        },
    ]
}


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter() -> SkyAdapter:
    return SkyAdapter()


def _mock_defillama(response: dict = _DEFILLAMA_RESPONSE):
    respx.get(settings.sky_savings_url).mock(return_value=Response(200, json=response))


# ---------------------------------------------------------------------------
# DSR tests
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_dsr_opportunity_type(adapter: SkyAdapter):
    """sDAI DSR: SAVINGS type, OVERNIGHT duration, SUPPLY side."""
    _mock_defillama()
    opps = await adapter.fetch_opportunities()
    dsr = next(
        (o for o in opps if o.opportunity_type == OpportunityType.SAVINGS and o.asset_id == "DAI"),
        None,
    )
    assert dsr is not None
    assert dsr.side == OpportunitySide.SUPPLY
    assert dsr.effective_duration == EffectiveDuration.OVERNIGHT


@respx.mock
@pytest.mark.asyncio
async def test_dsr_apy(adapter: SkyAdapter):
    """DSR APY passed through from DeFiLlama without modification."""
    _mock_defillama()
    opps = await adapter.fetch_opportunities()
    dsr = next(o for o in opps if o.opportunity_type == OpportunityType.SAVINGS and o.asset_id == "DAI")
    assert dsr.total_apy_pct == pytest.approx(5.25)
    assert dsr.base_apy_pct == pytest.approx(5.25)


@respx.mock
@pytest.mark.asyncio
async def test_dsr_receipt_token(adapter: SkyAdapter):
    """sDAI receipt token is ERC-4626, composable."""
    _mock_defillama()
    opps = await adapter.fetch_opportunities()
    dsr = next(o for o in opps if o.opportunity_type == OpportunityType.SAVINGS and o.asset_id == "DAI")
    r = dsr.receipt_token
    assert r is not None
    assert r.receipt_token_symbol == "sDAI"
    assert r.is_transferable is True
    assert r.is_composable is True


@respx.mock
@pytest.mark.asyncio
async def test_dsr_tvl(adapter: SkyAdapter):
    """TVL from DeFiLlama tvlUsd field."""
    _mock_defillama()
    opps = await adapter.fetch_opportunities()
    dsr = next(o for o in opps if o.opportunity_type == OpportunityType.SAVINGS and o.asset_id == "DAI")
    assert dsr.tvl_usd == pytest.approx(2_500_000_000)


# ---------------------------------------------------------------------------
# SSR tests
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_ssr_opportunity_type(adapter: SkyAdapter):
    """sUSDS SSR: SAVINGS type, OVERNIGHT, SUPPLY side."""
    _mock_defillama()
    opps = await adapter.fetch_opportunities()
    ssr = next(
        (o for o in opps if o.opportunity_type == OpportunityType.SAVINGS and o.asset_id == "USDS"),
        None,
    )
    assert ssr is not None
    assert ssr.side == OpportunitySide.SUPPLY
    assert ssr.effective_duration == EffectiveDuration.OVERNIGHT


@respx.mock
@pytest.mark.asyncio
async def test_ssr_apy(adapter: SkyAdapter):
    """SSR APY passed through correctly."""
    _mock_defillama()
    opps = await adapter.fetch_opportunities()
    ssr = next(o for o in opps if o.opportunity_type == OpportunityType.SAVINGS and o.asset_id == "USDS")
    assert ssr.total_apy_pct == pytest.approx(6.75)


@respx.mock
@pytest.mark.asyncio
async def test_ssr_receipt_token(adapter: SkyAdapter):
    """sUSDS receipt token is ERC-4626, composable."""
    _mock_defillama()
    opps = await adapter.fetch_opportunities()
    ssr = next(o for o in opps if o.opportunity_type == OpportunityType.SAVINGS and o.asset_id == "USDS")
    r = ssr.receipt_token
    assert r is not None
    assert r.receipt_token_symbol == "sUSDS"
    assert r.is_composable is True


# ---------------------------------------------------------------------------
# Venue separation
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_venue_is_sky_not_spark(adapter: SkyAdapter):
    """Opportunities are emitted under Venue.SKY, not SPARK."""
    _mock_defillama()
    opps = await adapter.fetch_opportunities()
    assert all(o.venue == "SKY" for o in opps)


@respx.mock
@pytest.mark.asyncio
async def test_market_ids_distinct_from_spark(adapter: SkyAdapter):
    """Sky market IDs do not collide with Spark market IDs."""
    _mock_defillama()
    opps = await adapter.fetch_opportunities()
    dsr = next(o for o in opps if o.asset_id == "DAI")
    ssr = next(o for o in opps if o.asset_id == "USDS")
    # Sky market IDs must start with 'sky:', not 'spark:'
    assert dsr.market_id.startswith("sky:")
    assert ssr.market_id.startswith("sky:")


# ---------------------------------------------------------------------------
# Chain and symbol filtering
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_non_ethereum_pools_skipped(adapter: SkyAdapter):
    """Arbitrum DSR pool is skipped; only one DAI savings opp returned."""
    _mock_defillama()
    opps = await adapter.fetch_opportunities()
    dai_opps = [o for o in opps if o.asset_id == "DAI"]
    assert len(dai_opps) == 1
    assert dai_opps[0].chain == "ETHEREUM"


@respx.mock
@pytest.mark.asyncio
async def test_unrelated_project_skipped(adapter: SkyAdapter):
    """Compound USDC pool is not included."""
    _mock_defillama()
    opps = await adapter.fetch_opportunities()
    usdc_opps = [o for o in opps if o.asset_id == "USDC"]
    assert len(usdc_opps) == 0


@respx.mock
@pytest.mark.asyncio
async def test_non_ethereum_chain_returns_empty(adapter: SkyAdapter):
    """Requesting Arbitrum chain returns empty list."""
    _mock_defillama()
    opps = await adapter.fetch_opportunities(chains=[Chain.ARBITRUM])
    assert opps == []


@respx.mock
@pytest.mark.asyncio
async def test_symbol_filter(adapter: SkyAdapter):
    """symbols=['DAI'] returns only the DSR opp, not SSR."""
    _mock_defillama()
    opps = await adapter.fetch_opportunities(symbols=["DAI"])
    assert all(o.asset_id == "DAI" for o in opps)
    usds_opps = [o for o in opps if o.asset_id == "USDS"]
    assert len(usds_opps) == 0


# ---------------------------------------------------------------------------
# Capacity and liquidity flags
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_no_cap_no_lockup(adapter: SkyAdapter):
    """Savings products are uncapped with no lock-up."""
    _mock_defillama()
    opps = await adapter.fetch_opportunities()
    for opp in opps:
        assert opp.is_capacity_capped is False
        assert opp.liquidity.has_lockup is False


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_health_check_ok(adapter: SkyAdapter):
    """Health check is ok when DSR pool is present."""
    _mock_defillama()
    result = await adapter.health_check()
    assert result["status"] == "ok"
    assert result["error"] is None


@respx.mock
@pytest.mark.asyncio
async def test_health_check_degraded_when_no_dsr(adapter: SkyAdapter):
    """Health check is degraded when no maker-dsr pool found."""
    _mock_defillama(_DEFILLAMA_NO_DSR)
    result = await adapter.health_check()
    assert result["status"] == "degraded"


@respx.mock
@pytest.mark.asyncio
async def test_health_check_down_on_error(adapter: SkyAdapter):
    """Health check is down when DeFiLlama is unreachable."""
    respx.get(settings.sky_savings_url).mock(side_effect=Exception("timeout"))
    result = await adapter.health_check()
    assert result["status"] == "down"
    assert result["error"] is not None


# ---------------------------------------------------------------------------
# Adapter static properties
# ---------------------------------------------------------------------------


def test_adapter_properties(adapter: SkyAdapter):
    assert adapter.venue == Venue.SKY
    assert adapter.protocol_name == "Sky"
    assert adapter.protocol_slug == "sky"
    assert adapter.refresh_interval_seconds == 3600
    assert adapter.requires_api_key is False
    assert adapter.api_key_env_var is None
    assert adapter.supported_chains == [Chain.ETHEREUM]
