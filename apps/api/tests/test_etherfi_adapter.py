"""
Tests for the EtherFi adapter (app.connectors.etherfi).

Covers:
  1. eETH staking — RESTAKING type, VARIABLE duration, asset=ETH
  2. Reward breakdown — NATIVE_YIELD + two POINTS components
  3. Receipt token — eETH, composable, weETH wrapping note
  4. TVL and total_supplied_usd populated
  5. EtherFi Liquid vaults — VAULT type, OVERNIGHT duration
  6. AMM LP vault pools are skipped
  7. Tiny pools (tvl < $10k) are skipped
  8. Only Ethereum pools processed; other chains skipped
  9. Chain guard — non-Ethereum chain returns empty
  10. Symbol filter respected
  11. Duplicate staking pools — only first eETH staking opp emitted
  12. Health check — ok, degraded, down
  13. Adapter static properties
"""
from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.connectors.etherfi import EtherFiAdapter
from app.core.config import settings
from asset_registry import Chain, Venue
from opportunity_schema import (
    EffectiveDuration,
    OpportunitySide,
    OpportunityType,
    RewardType,
)


# ---------------------------------------------------------------------------
# Mock DeFiLlama responses
# ---------------------------------------------------------------------------

_DEFILLAMA_RESPONSE = {
    "data": [
        # eETH staking pool — main product
        {
            "pool": "eeth-etherfi-ethereum",
            "chain": "Ethereum",
            "project": "ether.fi",
            "symbol": "ETH",
            "tvlUsd": 6_200_000_000,
            "apy": 4.1,
            "apyBase": 3.6,
            "apyReward": 0.5,
        },
        # EtherFi Liquid vault — USDC strategy
        {
            "pool": "etherfi-liquid-usdc-vault",
            "chain": "Ethereum",
            "project": "ether.fi-liquid",
            "symbol": "USDC",
            "poolMeta": "EtherFi Liquid USDC Vault",
            "tvlUsd": 85_000_000,
            "apy": 9.5,
        },
        # EtherFi Liquid vault — WETH strategy
        {
            "pool": "etherfi-liquid-weth-vault",
            "chain": "Ethereum",
            "project": "ether.fi-liquid",
            "symbol": "WETH",
            "poolMeta": "EtherFi Liquid ETH Vault",
            "tvlUsd": 50_000_000,
            "apy": 5.8,
        },
        # AMM LP — must be skipped
        {
            "pool": "etherfi-liquid-lp-pool",
            "chain": "Ethereum",
            "project": "ether.fi-liquid",
            "symbol": "WETH-USDC LP",
            "tvlUsd": 12_000_000,
            "apy": 15.0,
        },
        # Tiny pool — must be skipped (tvlUsd < 10_000)
        {
            "pool": "etherfi-liquid-tiny",
            "chain": "Ethereum",
            "project": "ether.fi-liquid",
            "symbol": "DAI",
            "tvlUsd": 5_000,
            "apy": 8.0,
        },
        # Non-Ethereum pool — must be skipped
        {
            "pool": "eeth-arbitrum",
            "chain": "Arbitrum",
            "project": "ether.fi",
            "symbol": "ETH",
            "tvlUsd": 200_000_000,
            "apy": 3.9,
        },
        # Unrelated project — must be skipped
        {
            "pool": "some-other",
            "chain": "Ethereum",
            "project": "lido",
            "symbol": "ETH",
            "tvlUsd": 10_000_000_000,
            "apy": 3.7,
        },
    ]
}

# Duplicate eETH staking entry — second one should be ignored
_DEFILLAMA_DUPE_STAKING = {
    "data": [
        {
            "pool": "eeth-first",
            "chain": "Ethereum",
            "project": "ether.fi",
            "symbol": "ETH",
            "tvlUsd": 6_200_000_000,
            "apy": 4.1,
            "apyBase": 3.6,
        },
        {
            "pool": "eeth-second",
            "chain": "Ethereum",
            "project": "ether.fi",
            "symbol": "ETH",
            "tvlUsd": 100_000_000,
            "apy": 3.0,
        },
    ]
}


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter() -> EtherFiAdapter:
    return EtherFiAdapter()


def _mock_defillama(response: dict = _DEFILLAMA_RESPONSE):
    respx.get(settings.defillama_yields_url).mock(
        return_value=Response(200, json=response)
    )


# ---------------------------------------------------------------------------
# eETH staking tests
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_staking_opportunity_type(adapter: EtherFiAdapter):
    """eETH staking: RESTAKING type, VARIABLE duration, SUPPLY side, asset=ETH."""
    _mock_defillama()
    opps = await adapter.fetch_opportunities()
    staking = next(
        (o for o in opps if o.opportunity_type == OpportunityType.RESTAKING),
        None,
    )
    assert staking is not None
    assert staking.side == OpportunitySide.SUPPLY
    assert staking.effective_duration == EffectiveDuration.VARIABLE
    assert staking.asset_id == "ETH"
    assert staking.asset_symbol == "ETH"
    assert staking.chain == "ETHEREUM"


@respx.mock
@pytest.mark.asyncio
async def test_staking_apy(adapter: EtherFiAdapter):
    """eETH: total_apy from apyBase + apyReward, base_apy from apyBase."""
    _mock_defillama()
    opps = await adapter.fetch_opportunities()
    staking = next(o for o in opps if o.opportunity_type == OpportunityType.RESTAKING)
    assert staking.total_apy_pct == pytest.approx(4.1)
    assert staking.base_apy_pct == pytest.approx(3.6)


@respx.mock
@pytest.mark.asyncio
async def test_staking_tvl(adapter: EtherFiAdapter):
    """TVL and total_supplied_usd from tvlUsd."""
    _mock_defillama()
    opps = await adapter.fetch_opportunities()
    staking = next(o for o in opps if o.opportunity_type == OpportunityType.RESTAKING)
    assert staking.tvl_usd == pytest.approx(6_200_000_000)
    assert staking.total_supplied_usd == pytest.approx(6_200_000_000)


@respx.mock
@pytest.mark.asyncio
async def test_staking_reward_breakdown(adapter: EtherFiAdapter):
    """Reward breakdown: NATIVE_YIELD + EtherFi POINTS + EigenLayer POINTS."""
    _mock_defillama()
    opps = await adapter.fetch_opportunities()
    staking = next(o for o in opps if o.opportunity_type == OpportunityType.RESTAKING)

    reward_types = [r.reward_type for r in staking.reward_breakdown]
    assert RewardType.NATIVE_YIELD in reward_types

    points = [r for r in staking.reward_breakdown if r.reward_type == RewardType.POINTS]
    assert len(points) == 2

    point_names = {r.token_name for r in points}
    assert "EtherFi Points" in point_names
    assert "EigenLayer Points" in point_names

    # Points have no numeric APY
    for p in points:
        assert p.apy_pct == pytest.approx(0.0)


@respx.mock
@pytest.mark.asyncio
async def test_staking_receipt_token(adapter: EtherFiAdapter):
    """Receipt token is eETH — transferable, composable, weETH note present."""
    _mock_defillama()
    opps = await adapter.fetch_opportunities()
    staking = next(o for o in opps if o.opportunity_type == OpportunityType.RESTAKING)
    r = staking.receipt_token
    assert r is not None
    assert r.receipt_token_symbol == "eETH"
    assert r.is_transferable is True
    assert r.is_composable is True
    notes = r.notes or ""
    assert "weETH" in notes


@respx.mock
@pytest.mark.asyncio
async def test_staking_tags(adapter: EtherFiAdapter):
    """Tags include restaking, eigenlayer, points."""
    _mock_defillama()
    opps = await adapter.fetch_opportunities()
    staking = next(o for o in opps if o.opportunity_type == OpportunityType.RESTAKING)
    assert "liquid-restaking" in staking.tags
    assert "eigenlayer" in staking.tags
    assert "points" in staking.tags


# ---------------------------------------------------------------------------
# EtherFi Liquid vault tests
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_vault_opportunities(adapter: EtherFiAdapter):
    """EtherFi Liquid pools produce VAULT type, OVERNIGHT duration."""
    _mock_defillama()
    opps = await adapter.fetch_opportunities()
    vaults = [o for o in opps if o.opportunity_type == OpportunityType.VAULT]
    # WETH-USDC LP is skipped; tiny DAI pool is skipped → 2 vault opps remain
    assert len(vaults) == 2
    for v in vaults:
        assert v.side == OpportunitySide.SUPPLY
        assert v.effective_duration == EffectiveDuration.OVERNIGHT
        assert v.chain == "ETHEREUM"


@respx.mock
@pytest.mark.asyncio
async def test_vault_apy(adapter: EtherFiAdapter):
    """Vault APY passed through from DeFiLlama."""
    _mock_defillama()
    opps = await adapter.fetch_opportunities()
    usdc_vault = next(
        (o for o in opps if o.opportunity_type == OpportunityType.VAULT and o.asset_id == "USDC"),
        None,
    )
    assert usdc_vault is not None
    assert usdc_vault.total_apy_pct == pytest.approx(9.5)


@respx.mock
@pytest.mark.asyncio
async def test_vault_eigenlayer_points(adapter: EtherFiAdapter):
    """Vault reward_breakdown includes EigenLayer POINTS component."""
    _mock_defillama()
    opps = await adapter.fetch_opportunities()
    vaults = [o for o in opps if o.opportunity_type == OpportunityType.VAULT]
    for v in vaults:
        has_eigenlayer_points = any(
            r.reward_type == RewardType.POINTS and r.token_name == "EigenLayer Points"
            for r in v.reward_breakdown
        )
        assert has_eigenlayer_points


@respx.mock
@pytest.mark.asyncio
async def test_vault_market_name(adapter: EtherFiAdapter):
    """Vault market_name uses poolMeta when available."""
    _mock_defillama()
    opps = await adapter.fetch_opportunities()
    usdc_vault = next(
        o for o in opps if o.opportunity_type == OpportunityType.VAULT and o.asset_id == "USDC"
    )
    assert "USDC" in usdc_vault.market_name


# ---------------------------------------------------------------------------
# Filtering / skipping tests
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_amm_lp_pool_skipped(adapter: EtherFiAdapter):
    """Pool with 'LP' in symbol is skipped."""
    _mock_defillama()
    opps = await adapter.fetch_opportunities()
    lp_opps = [o for o in opps if "LP" in (o.asset_symbol or "")]
    assert len(lp_opps) == 0


@respx.mock
@pytest.mark.asyncio
async def test_tiny_pool_skipped(adapter: EtherFiAdapter):
    """Pool with tvlUsd < 10_000 is skipped."""
    _mock_defillama()
    opps = await adapter.fetch_opportunities()
    # DAI tiny pool should not appear
    dai_opps = [o for o in opps if o.asset_id == "DAI"]
    assert len(dai_opps) == 0


@respx.mock
@pytest.mark.asyncio
async def test_non_ethereum_pool_skipped(adapter: EtherFiAdapter):
    """Arbitrum ether.fi pool is not included."""
    _mock_defillama()
    opps = await adapter.fetch_opportunities()
    assert all(o.chain == "ETHEREUM" for o in opps)


@respx.mock
@pytest.mark.asyncio
async def test_unrelated_project_skipped(adapter: EtherFiAdapter):
    """Lido pool is not included."""
    _mock_defillama()
    opps = await adapter.fetch_opportunities()
    # Lido stETH at 3.7% APY would have STAKING type; verify it's absent
    staking_opps = [o for o in opps if o.opportunity_type == OpportunityType.RESTAKING]
    assert len(staking_opps) == 1  # Only the ether.fi one


@respx.mock
@pytest.mark.asyncio
async def test_duplicate_staking_deduplicated(adapter: EtherFiAdapter):
    """Only the first eETH staking pool is used; duplicate is ignored."""
    _mock_defillama(_DEFILLAMA_DUPE_STAKING)
    opps = await adapter.fetch_opportunities()
    staking_opps = [o for o in opps if o.opportunity_type == OpportunityType.RESTAKING]
    assert len(staking_opps) == 1
    # First pool (4.1%) should win, not the second (3.0%)
    assert staking_opps[0].total_apy_pct == pytest.approx(4.1)


# ---------------------------------------------------------------------------
# Chain and symbol filtering
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_non_ethereum_chain_returns_empty(adapter: EtherFiAdapter):
    """Requesting Arbitrum returns empty list."""
    _mock_defillama()
    opps = await adapter.fetch_opportunities(chains=[Chain.ARBITRUM])
    assert opps == []


@respx.mock
@pytest.mark.asyncio
async def test_symbol_filter_eth(adapter: EtherFiAdapter):
    """symbols=['ETH'] returns only the eETH staking opp."""
    _mock_defillama()
    opps = await adapter.fetch_opportunities(symbols=["ETH"])
    assert all(o.asset_id == "ETH" for o in opps)
    restaking = [o for o in opps if o.opportunity_type == OpportunityType.RESTAKING]
    assert len(restaking) == 1


@respx.mock
@pytest.mark.asyncio
async def test_symbol_filter_usdc(adapter: EtherFiAdapter):
    """symbols=['USDC'] returns only the USDC vault opp."""
    _mock_defillama()
    opps = await adapter.fetch_opportunities(symbols=["USDC"])
    assert all(o.asset_id == "USDC" for o in opps)
    assert all(o.opportunity_type == OpportunityType.VAULT for o in opps)


# ---------------------------------------------------------------------------
# Capacity and liquidity flags
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_no_supply_cap(adapter: EtherFiAdapter):
    """eETH staking has no supply cap."""
    _mock_defillama()
    opps = await adapter.fetch_opportunities()
    staking = next(o for o in opps if o.opportunity_type == OpportunityType.RESTAKING)
    assert staking.is_capacity_capped is False


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_health_check_ok(adapter: EtherFiAdapter):
    """Health check ok when ether.fi pool found."""
    _mock_defillama()
    result = await adapter.health_check()
    assert result["status"] == "ok"
    assert result["error"] is None


@respx.mock
@pytest.mark.asyncio
async def test_health_check_degraded_no_pools(adapter: EtherFiAdapter):
    """Health check degraded when no ether.fi Ethereum pools exist."""
    respx.get(settings.defillama_yields_url).mock(
        return_value=Response(200, json={"data": [
            {"pool": "other", "chain": "Ethereum", "project": "lido", "symbol": "ETH", "apy": 3.5}
        ]})
    )
    result = await adapter.health_check()
    assert result["status"] == "degraded"


@respx.mock
@pytest.mark.asyncio
async def test_health_check_down(adapter: EtherFiAdapter):
    """Health check down when DeFiLlama is unreachable."""
    respx.get(settings.defillama_yields_url).mock(
        side_effect=Exception("network error")
    )
    result = await adapter.health_check()
    assert result["status"] == "down"
    assert result["error"] is not None


# ---------------------------------------------------------------------------
# Adapter static properties
# ---------------------------------------------------------------------------


def test_adapter_properties(adapter: EtherFiAdapter):
    assert adapter.venue == Venue.ETHERFI
    assert adapter.protocol_name == "EtherFi"
    assert adapter.protocol_slug == "ether.fi"
    assert adapter.refresh_interval_seconds == 900
    assert adapter.requires_api_key is False
    assert adapter.api_key_env_var is None
    assert adapter.supported_chains == [Chain.ETHEREUM]
