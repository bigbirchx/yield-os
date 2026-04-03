"""
Tests for the Jupiter adapter (app.connectors.jupiter).

Covers:
  1. JLP — VAULT type, not AMM_LP, correct APY and TVL
  2. JupSOL — SAVINGS type, liquid staking receipt token
  3. Jupiter Lend markets — LENDING type, SUPPLY only
  4. AMM LP symbols in Lend are skipped
  5. Chain filtering (Solana only)
  6. Adapter static properties
"""
from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.connectors.jupiter import JupiterAdapter
from app.core.config import settings
from asset_registry import Chain, Venue
from opportunity_schema import EffectiveDuration, OpportunitySide, OpportunityType


# ---------------------------------------------------------------------------
# Mock response
# ---------------------------------------------------------------------------

_DEFILLAMA_RESPONSE = {
    "data": [
        # Jupiter Lend: USDC
        {
            "pool": "d783c8df-usdc-111111111111",
            "project": "jupiter-lend",
            "chain": "Solana",
            "symbol": "USDC",
            "apy": 3.72,
            "tvlUsd": 471_954_608,
            "underlyingTokens": ["EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"],
        },
        # Jupiter Lend: WSOL (maps to SOL)
        {
            "pool": "86d5dc3c-wsol-222222222222",
            "project": "jupiter-lend",
            "chain": "Solana",
            "symbol": "WSOL",
            "apy": 5.99,
            "tvlUsd": 65_844_309,
            "underlyingTokens": ["So11111111111111111111111111111111111111112"],
        },
        # Jupiter Lend: JLP vault
        {
            "pool": "cf41a15b-jlp-333333333333",
            "project": "jupiter-lend",
            "chain": "Solana",
            "symbol": "JLP",
            "apy": 10.02,
            "tvlUsd": 82_926_485,
            "underlyingTokens": ["27G8MtK7VtTcCHkpASjSDdkWWYfoqT6ggEuKidVJidD4"],
        },
        # Jupiter Staked SOL
        {
            "pool": "52bd72a7-jupsol-444444444444",
            "project": "jupiter-staked-sol",
            "chain": "Solana",
            "symbol": "JUPSOL",
            "apy": 6.21,
            "tvlUsd": 338_869_572,
            "underlyingTokens": ["jupSoLaHXQiZZTSfEWMTRRgpnyFm8f6sZdosWBjx93v"],
        },
        # Jupiter Lend: LBTC
        {
            "pool": "b9f8f1ba-lbtc-555555555555",
            "project": "jupiter-lend",
            "chain": "Solana",
            "symbol": "LBTC",
            "apy": 0.38,
            "tvlUsd": 18_447_676,
            "underlyingTokens": [],
        },
        # Non-Solana pool — should be ignored
        {
            "pool": "ethereum-pool",
            "project": "jupiter-lend",
            "chain": "Ethereum",
            "symbol": "USDC",
            "apy": 3.5,
            "tvlUsd": 100_000_000,
        },
        # Unknown project — ignored
        {
            "pool": "other-pool",
            "project": "aave-v3",
            "chain": "Solana",
            "symbol": "USDC",
            "apy": 5.0,
            "tvlUsd": 200_000_000,
        },
        # Tiny pool — below TVL threshold
        {
            "pool": "tiny-pool",
            "project": "jupiter-lend",
            "chain": "Solana",
            "symbol": "MICRO",
            "apy": 50.0,
            "tvlUsd": 500,
        },
    ]
}

DEFILLAMA_URL = settings.defillama_yields_url


@pytest.fixture
def adapter() -> JupiterAdapter:
    return JupiterAdapter()


# ---------------------------------------------------------------------------
# JLP vault tests
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_jlp_is_vault_not_amm_lp(adapter: JupiterAdapter):
    """JLP pool is VAULT type and is_amm_lp=False (not an AMM pair)."""
    respx.get(DEFILLAMA_URL).mock(return_value=Response(200, json=_DEFILLAMA_RESPONSE))
    opps = await adapter.fetch_opportunities()
    jlp = next(o for o in opps if o.asset_id == "JLP")

    assert jlp.opportunity_type == OpportunityType.VAULT
    assert jlp.side == OpportunitySide.SUPPLY
    assert jlp.is_amm_lp is False


@respx.mock
@pytest.mark.asyncio
async def test_jlp_apy_and_tvl(adapter: JupiterAdapter):
    """JLP APY and TVL from DeFiLlama are used as-is (already in %)."""
    respx.get(DEFILLAMA_URL).mock(return_value=Response(200, json=_DEFILLAMA_RESPONSE))
    opps = await adapter.fetch_opportunities()
    jlp = next(o for o in opps if o.asset_id == "JLP")

    assert jlp.total_apy_pct == pytest.approx(10.02)
    assert jlp.tvl_usd == pytest.approx(82_926_485)


@respx.mock
@pytest.mark.asyncio
async def test_jlp_receipt_token(adapter: JupiterAdapter):
    """JLP receipt token symbol is JLP, composable."""
    respx.get(DEFILLAMA_URL).mock(return_value=Response(200, json=_DEFILLAMA_RESPONSE))
    opps = await adapter.fetch_opportunities()
    jlp = next(o for o in opps if o.asset_id == "JLP")

    r = jlp.receipt_token
    assert r is not None
    assert r.receipt_token_symbol == "JLP"
    assert r.is_composable is True


@respx.mock
@pytest.mark.asyncio
async def test_jlp_effective_duration_overnight(adapter: JupiterAdapter):
    """JLP vault has no lockup — OVERNIGHT duration."""
    respx.get(DEFILLAMA_URL).mock(return_value=Response(200, json=_DEFILLAMA_RESPONSE))
    opps = await adapter.fetch_opportunities()
    jlp = next(o for o in opps if o.asset_id == "JLP")
    assert jlp.effective_duration == EffectiveDuration.OVERNIGHT


# ---------------------------------------------------------------------------
# JupSOL tests
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_jupsol_is_savings(adapter: JupiterAdapter):
    """JupSOL is SAVINGS type, OVERNIGHT duration, SUPPLY side."""
    respx.get(DEFILLAMA_URL).mock(return_value=Response(200, json=_DEFILLAMA_RESPONSE))
    opps = await adapter.fetch_opportunities()
    jupsol = next(o for o in opps if o.asset_id == "JupSOL")

    assert jupsol.opportunity_type == OpportunityType.SAVINGS
    assert jupsol.side == OpportunitySide.SUPPLY
    assert jupsol.effective_duration == EffectiveDuration.OVERNIGHT
    assert jupsol.total_apy_pct == pytest.approx(6.21)
    assert jupsol.venue == "JUPITER"


@respx.mock
@pytest.mark.asyncio
async def test_jupsol_receipt_token(adapter: JupiterAdapter):
    """JupSOL receipt token symbol is JupSOL."""
    respx.get(DEFILLAMA_URL).mock(return_value=Response(200, json=_DEFILLAMA_RESPONSE))
    opps = await adapter.fetch_opportunities()
    jupsol = next(o for o in opps if o.asset_id == "JupSOL")

    r = jupsol.receipt_token
    assert r is not None
    assert r.receipt_token_symbol == "JupSOL"


# ---------------------------------------------------------------------------
# Jupiter Lend tests
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_lend_usdc_supply(adapter: JupiterAdapter):
    """Jupiter Lend USDC is LENDING type, SUPPLY side."""
    respx.get(DEFILLAMA_URL).mock(return_value=Response(200, json=_DEFILLAMA_RESPONSE))
    opps = await adapter.fetch_opportunities()
    usdc = next(
        o for o in opps
        if o.asset_id == "USDC" and o.opportunity_type == OpportunityType.LENDING
    )
    assert usdc.side == OpportunitySide.SUPPLY
    assert usdc.effective_duration == EffectiveDuration.VARIABLE
    assert usdc.total_apy_pct == pytest.approx(3.72)
    assert usdc.tvl_usd == pytest.approx(471_954_608)


@respx.mock
@pytest.mark.asyncio
async def test_lend_wsol_normalizes_to_sol(adapter: JupiterAdapter):
    """WSOL in DeFiLlama normalizes to canonical SOL asset_id."""
    respx.get(DEFILLAMA_URL).mock(return_value=Response(200, json=_DEFILLAMA_RESPONSE))
    opps = await adapter.fetch_opportunities()
    sol_lend = next(
        (o for o in opps
         if o.asset_symbol == "WSOL" and o.opportunity_type == OpportunityType.LENDING),
        None,
    )
    assert sol_lend is not None
    assert sol_lend.asset_id == "SOL"


@respx.mock
@pytest.mark.asyncio
async def test_lend_lbtc(adapter: JupiterAdapter):
    """Jupiter Lend LBTC is modelled correctly."""
    respx.get(DEFILLAMA_URL).mock(return_value=Response(200, json=_DEFILLAMA_RESPONSE))
    opps = await adapter.fetch_opportunities()
    lbtc = next(
        (o for o in opps
         if o.asset_id == "LBTC" and o.opportunity_type == OpportunityType.LENDING),
        None,
    )
    assert lbtc is not None
    assert lbtc.total_apy_pct == pytest.approx(0.38)


@respx.mock
@pytest.mark.asyncio
async def test_tiny_pool_skipped(adapter: JupiterAdapter):
    """Pools with TVL < $1000 are skipped."""
    respx.get(DEFILLAMA_URL).mock(return_value=Response(200, json=_DEFILLAMA_RESPONSE))
    opps = await adapter.fetch_opportunities()
    micro_opps = [o for o in opps if o.asset_symbol == "MICRO"]
    assert len(micro_opps) == 0


# ---------------------------------------------------------------------------
# Chain filtering
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_non_solana_returns_empty(adapter: JupiterAdapter):
    """Jupiter only supports Solana; other chains return empty list."""
    respx.get(DEFILLAMA_URL).mock(return_value=Response(200, json=_DEFILLAMA_RESPONSE))
    opps = await adapter.fetch_opportunities(chains=[Chain.ETHEREUM])
    assert opps == []


@respx.mock
@pytest.mark.asyncio
async def test_non_solana_pools_in_defillama_ignored(adapter: JupiterAdapter):
    """Ethereum-chain pools from DeFiLlama are ignored even with chains=None."""
    respx.get(DEFILLAMA_URL).mock(return_value=Response(200, json=_DEFILLAMA_RESPONSE))
    opps = await adapter.fetch_opportunities()
    # All opportunities must be on SOLANA
    assert all(o.chain == "SOLANA" for o in opps)


# ---------------------------------------------------------------------------
# Adapter properties
# ---------------------------------------------------------------------------


def test_adapter_properties(adapter: JupiterAdapter):
    assert adapter.venue == Venue.JUPITER
    assert adapter.protocol_name == "Jupiter"
    assert adapter.protocol_slug == "jupiter"
    assert adapter.refresh_interval_seconds == 300
    assert adapter.requires_api_key is False
    assert adapter.supported_chains == [Chain.SOLANA]


@respx.mock
@pytest.mark.asyncio
async def test_health_check_ok(adapter: JupiterAdapter):
    respx.get(DEFILLAMA_URL).mock(return_value=Response(200, json=_DEFILLAMA_RESPONSE))
    result = await adapter.health_check()
    assert result["status"] == "ok"
    assert result["error"] is None
