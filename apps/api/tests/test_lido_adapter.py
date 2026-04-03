"""
Tests for the Lido liquid-staking adapter (app.connectors.lido).

Covers:
  1. Staking opportunity — STAKING type, VARIABLE duration, asset=ETH
  2. APR extraction — handles smaApr, 7dSmaApr, averageApr field aliases
  3. Receipt token — stETH, composable (wstETH note)
  4. Withdrawal queue — has_withdrawal_queue=True, queue length estimated
  5. TVL from stats endpoint
  6. ETH symbol filter respected
  7. Non-Ethereum chain returns empty
  8. Graceful degradation — APR call fails → empty list
  9. Stats / queue failures → still returns opp (optional data)
  10. Health check — ok, degraded, down
  11. Adapter static properties
"""
from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.connectors.lido import LidoAdapter
from asset_registry import Chain, Venue
from opportunity_schema import EffectiveDuration, OpportunitySide, OpportunityType


# ---------------------------------------------------------------------------
# Mock responses
# ---------------------------------------------------------------------------

_APR_RESPONSE = {
    "data": {
        "smaApr": 3.75,
        "7dSmaApr": 3.75,
        "30dSmaApr": 3.60,
    }
}

_STATS_RESPONSE = {
    "data": {
        "totalStaked": 9_600_000.0,       # ETH (not Wei, small float)
        "totalStakedUsd": 28_800_000_000.0,
    }
}

# totalStaked in Wei (large integer string) — alternate encoding
_STATS_WEI_RESPONSE = {
    "data": {
        "totalStaked": "9600000000000000000000000",   # 9.6e6 ETH in Wei
        "totalStakedUsd": 28_800_000_000.0,
    }
}

_QUEUE_RESPONSE = {
    "data": {
        "unfinalizedStETH": "96000000000000000000000",   # ~96 000 ETH in Wei
        "unfinalizedRequestsCount": 4200,
    }
}

_QUEUE_SMALL = {
    "data": {
        "unfinalizedStETH": "9600000000000000000000",    # ~9 600 ETH in Wei
        "unfinalizedRequestsCount": 120,
    }
}


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter() -> LidoAdapter:
    return LidoAdapter()


def _mock_all(adapter: LidoAdapter, queue: dict = _QUEUE_RESPONSE):
    base = adapter._api_base
    respx.get(f"{base}/v1/protocol/steth/apr/sma").mock(
        return_value=Response(200, json=_APR_RESPONSE)
    )
    respx.get(f"{base}/v1/protocol/steth/stats").mock(
        return_value=Response(200, json=_STATS_RESPONSE)
    )
    respx.get(f"{base}/v1/protocol/withdrawal-queue/stats").mock(
        return_value=Response(200, json=queue)
    )


# ---------------------------------------------------------------------------
# Core opportunity shape
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_staking_opportunity_type(adapter: LidoAdapter):
    """Lido emits a STAKING, VARIABLE, SUPPLY opportunity for ETH."""
    _mock_all(adapter)
    opps = await adapter.fetch_opportunities()
    assert len(opps) == 1
    opp = opps[0]
    assert opp.opportunity_type == OpportunityType.STAKING
    assert opp.effective_duration == EffectiveDuration.VARIABLE
    assert opp.side == OpportunitySide.SUPPLY
    assert opp.asset_id == "ETH"
    assert opp.asset_symbol == "ETH"
    assert opp.chain == "ETHEREUM"


@respx.mock
@pytest.mark.asyncio
async def test_staking_apy(adapter: LidoAdapter):
    """APY populated from smaApr field."""
    _mock_all(adapter)
    opps = await adapter.fetch_opportunities()
    assert opps[0].total_apy_pct == pytest.approx(3.75)
    assert opps[0].base_apy_pct == pytest.approx(3.75)


@respx.mock
@pytest.mark.asyncio
async def test_staking_tvl(adapter: LidoAdapter):
    """TVL and total_supplied_usd from stats endpoint."""
    _mock_all(adapter)
    opps = await adapter.fetch_opportunities()
    opp = opps[0]
    assert opp.tvl_usd == pytest.approx(28_800_000_000)
    assert opp.total_supplied_usd == pytest.approx(28_800_000_000)
    assert opp.total_supplied == pytest.approx(9_600_000.0)


@respx.mock
@pytest.mark.asyncio
async def test_staking_total_supplied_wei(adapter: LidoAdapter):
    """totalStaked in Wei is correctly converted to ETH."""
    base = adapter._api_base
    respx.get(f"{base}/v1/protocol/steth/apr/sma").mock(
        return_value=Response(200, json=_APR_RESPONSE)
    )
    respx.get(f"{base}/v1/protocol/steth/stats").mock(
        return_value=Response(200, json=_STATS_WEI_RESPONSE)
    )
    respx.get(f"{base}/v1/protocol/withdrawal-queue/stats").mock(
        return_value=Response(200, json=_QUEUE_RESPONSE)
    )
    opps = await adapter.fetch_opportunities()
    assert opps[0].total_supplied == pytest.approx(9_600_000.0, rel=1e-3)


# ---------------------------------------------------------------------------
# Receipt token
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_receipt_token(adapter: LidoAdapter):
    """Receipt token is stETH — transferable, composable."""
    _mock_all(adapter)
    opps = await adapter.fetch_opportunities()
    r = opps[0].receipt_token
    assert r is not None
    assert r.receipt_token_symbol == "stETH"
    assert r.is_transferable is True
    assert r.is_composable is True


@respx.mock
@pytest.mark.asyncio
async def test_receipt_token_mentions_wsteth(adapter: LidoAdapter):
    """Receipt token notes mention wstETH wrapping for DeFi collateral."""
    _mock_all(adapter)
    opp = (await adapter.fetch_opportunities())[0]
    notes = opp.receipt_token.notes or ""
    assert "wstETH" in notes


# ---------------------------------------------------------------------------
# Withdrawal queue
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_withdrawal_queue_flag(adapter: LidoAdapter):
    """LiquidityInfo has_withdrawal_queue=True."""
    _mock_all(adapter)
    opp = (await adapter.fetch_opportunities())[0]
    assert opp.liquidity.has_withdrawal_queue is True


@respx.mock
@pytest.mark.asyncio
async def test_withdrawal_queue_length_estimated(adapter: LidoAdapter):
    """current_queue_length_days is a positive float when queue is non-zero."""
    _mock_all(adapter, queue=_QUEUE_RESPONSE)
    opp = (await adapter.fetch_opportunities())[0]
    # 96 000 ETH unfinalized / (9.6e6 × 0.01%) = 96 000 / 960 = 100 days (approx)
    days = opp.liquidity.current_queue_length_days
    assert days is not None
    assert days > 0.0


@respx.mock
@pytest.mark.asyncio
async def test_short_queue_length(adapter: LidoAdapter):
    """Smaller queue yields shorter estimated wait."""
    _mock_all(adapter, queue=_QUEUE_SMALL)
    opp = (await adapter.fetch_opportunities())[0]
    days_short = opp.liquidity.current_queue_length_days

    _mock_all(adapter, queue=_QUEUE_RESPONSE)
    opp2 = (await adapter.fetch_opportunities())[0]
    days_long = opp2.liquidity.current_queue_length_days

    assert days_short is not None and days_long is not None
    assert days_short < days_long


# ---------------------------------------------------------------------------
# APR field alias fallback
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_apr_alias_7d(adapter: LidoAdapter):
    """7dSmaApr alias used when smaApr is absent."""
    base = adapter._api_base
    respx.get(f"{base}/v1/protocol/steth/apr/sma").mock(
        return_value=Response(200, json={"data": {"7dSmaApr": 3.80}})
    )
    respx.get(f"{base}/v1/protocol/steth/stats").mock(
        return_value=Response(200, json=_STATS_RESPONSE)
    )
    respx.get(f"{base}/v1/protocol/withdrawal-queue/stats").mock(
        return_value=Response(200, json=_QUEUE_RESPONSE)
    )
    opps = await adapter.fetch_opportunities()
    assert opps[0].total_apy_pct == pytest.approx(3.80)


@respx.mock
@pytest.mark.asyncio
async def test_apr_alias_averageApr(adapter: LidoAdapter):
    """averageApr alias used as last resort."""
    base = adapter._api_base
    respx.get(f"{base}/v1/protocol/steth/apr/sma").mock(
        return_value=Response(200, json={"data": {"averageApr": 3.55}})
    )
    respx.get(f"{base}/v1/protocol/steth/stats").mock(
        return_value=Response(200, json=_STATS_RESPONSE)
    )
    respx.get(f"{base}/v1/protocol/withdrawal-queue/stats").mock(
        return_value=Response(200, json=_QUEUE_RESPONSE)
    )
    opps = await adapter.fetch_opportunities()
    assert opps[0].total_apy_pct == pytest.approx(3.55)


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_apr_fetch_failure_returns_empty(adapter: LidoAdapter):
    """If APR endpoint fails, fetch_opportunities returns empty list (not crash)."""
    base = adapter._api_base
    respx.get(f"{base}/v1/protocol/steth/apr/sma").mock(
        side_effect=Exception("APR endpoint down")
    )
    respx.get(f"{base}/v1/protocol/steth/stats").mock(
        return_value=Response(200, json=_STATS_RESPONSE)
    )
    respx.get(f"{base}/v1/protocol/withdrawal-queue/stats").mock(
        return_value=Response(200, json=_QUEUE_RESPONSE)
    )
    opps = await adapter.fetch_opportunities()
    assert opps == []


@respx.mock
@pytest.mark.asyncio
async def test_stats_failure_still_returns_opp(adapter: LidoAdapter):
    """Stats endpoint failure: opp is still returned with None TVL."""
    base = adapter._api_base
    respx.get(f"{base}/v1/protocol/steth/apr/sma").mock(
        return_value=Response(200, json=_APR_RESPONSE)
    )
    respx.get(f"{base}/v1/protocol/steth/stats").mock(
        side_effect=Exception("stats down")
    )
    respx.get(f"{base}/v1/protocol/withdrawal-queue/stats").mock(
        return_value=Response(200, json=_QUEUE_RESPONSE)
    )
    opps = await adapter.fetch_opportunities()
    assert len(opps) == 1
    assert opps[0].tvl_usd is None


@respx.mock
@pytest.mark.asyncio
async def test_queue_failure_still_returns_opp(adapter: LidoAdapter):
    """Queue endpoint failure: opp returned with has_withdrawal_queue=True, days=None."""
    base = adapter._api_base
    respx.get(f"{base}/v1/protocol/steth/apr/sma").mock(
        return_value=Response(200, json=_APR_RESPONSE)
    )
    respx.get(f"{base}/v1/protocol/steth/stats").mock(
        return_value=Response(200, json=_STATS_RESPONSE)
    )
    respx.get(f"{base}/v1/protocol/withdrawal-queue/stats").mock(
        side_effect=Exception("queue api down")
    )
    opps = await adapter.fetch_opportunities()
    assert len(opps) == 1
    liq = opps[0].liquidity
    assert liq.has_withdrawal_queue is True
    assert liq.current_queue_length_days is None


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_non_ethereum_chain_empty(adapter: LidoAdapter):
    """Lido is ETH-only; requesting Arbitrum returns empty."""
    _mock_all(adapter)
    opps = await adapter.fetch_opportunities(chains=[Chain.ARBITRUM])
    assert opps == []


@respx.mock
@pytest.mark.asyncio
async def test_symbol_filter_eth(adapter: LidoAdapter):
    """symbols=['ETH'] returns the single stETH opp."""
    _mock_all(adapter)
    opps = await adapter.fetch_opportunities(symbols=["ETH"])
    assert len(opps) == 1


@respx.mock
@pytest.mark.asyncio
async def test_symbol_filter_non_eth(adapter: LidoAdapter):
    """symbols=['USDC'] returns empty (Lido only provides ETH staking)."""
    _mock_all(adapter)
    opps = await adapter.fetch_opportunities(symbols=["USDC"])
    assert opps == []


# ---------------------------------------------------------------------------
# Capacity flags
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_no_supply_cap(adapter: LidoAdapter):
    """Lido has no supply cap."""
    _mock_all(adapter)
    opp = (await adapter.fetch_opportunities())[0]
    assert opp.is_capacity_capped is False


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_health_check_ok(adapter: LidoAdapter):
    """Health check ok when APR endpoint returns valid data."""
    base = adapter._api_base
    respx.get(f"{base}/v1/protocol/steth/apr/sma").mock(
        return_value=Response(200, json=_APR_RESPONSE)
    )
    result = await adapter.health_check()
    assert result["status"] == "ok"
    assert result["error"] is None


@respx.mock
@pytest.mark.asyncio
async def test_health_check_degraded(adapter: LidoAdapter):
    """Health check degraded when APR is missing from response."""
    base = adapter._api_base
    respx.get(f"{base}/v1/protocol/steth/apr/sma").mock(
        return_value=Response(200, json={"data": {}})
    )
    result = await adapter.health_check()
    assert result["status"] == "degraded"


@respx.mock
@pytest.mark.asyncio
async def test_health_check_down(adapter: LidoAdapter):
    """Health check down when APR endpoint is unreachable."""
    base = adapter._api_base
    respx.get(f"{base}/v1/protocol/steth/apr/sma").mock(
        side_effect=Exception("connection refused")
    )
    result = await adapter.health_check()
    assert result["status"] == "down"
    assert result["error"] is not None


# ---------------------------------------------------------------------------
# Adapter static properties
# ---------------------------------------------------------------------------


def test_adapter_properties(adapter: LidoAdapter):
    assert adapter.venue == Venue.LIDO
    assert adapter.protocol_name == "Lido"
    assert adapter.protocol_slug == "lido"
    assert adapter.refresh_interval_seconds == 3600
    assert adapter.requires_api_key is False
    assert adapter.api_key_env_var is None
    assert adapter.supported_chains == [Chain.ETHEREUM]
