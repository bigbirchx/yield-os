"""
Tests for the Pendle adapter (app.connectors.pendle).

Covers:
  1. PT opportunity — PENDLE_PT type, FIXED_TERM, correct APY, is_pendle=True
  2. YT opportunity — PENDLE_YT type, FIXED_TERM, correct APY (incl. negative)
  3. Maturity filtering — already-expired markets are skipped
  4. Liquidity filter — markets below $100k USD are skipped
  5. Inactive markets — isActive=False are skipped
  6. Pagination — adapter follows skip/total to fetch multiple pages
  7. Symbol filtering — symbols kwarg narrows results
  8. Chain filtering — only ETHEREUM/ARBITRUM/BSC supported
  9. Multi-chain — concurrent fetch across all three chains
  10. Adapter static properties
  11. Health check
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import respx
from httpx import Response

from app.connectors.pendle import PendleAdapter
from app.core.config import settings
from asset_registry import Chain, Venue
from opportunity_schema import EffectiveDuration, OpportunitySide, OpportunityType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _future_expiry(days: int = 90) -> str:
    dt = datetime.now(UTC) + timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _past_expiry() -> str:
    dt = datetime.now(UTC) - timedelta(days=1)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _market(
    address: str = "0xabc",
    symbol: str = "stETH",
    implied_apy: float = 0.053,
    yt_apy: float = 0.021,
    underlying_apy: float = 0.04,
    liq_usd: float = 5_000_000.0,
    is_active: bool = True,
    days: int = 90,
    simple_name: str | None = None,
) -> dict:
    expiry = _future_expiry(days)
    return {
        "address": address,
        "expiry": expiry,
        "isActive": is_active,
        "simpleName": simple_name or symbol,
        "impliedApy": implied_apy,
        "ytFloatingApy": yt_apy,
        "underlyingApy": underlying_apy,
        "liquidity": {"usd": liq_usd},
        "underlyingAsset": {"symbol": symbol, "address": "0xunderlying"},
        "pt": {"symbol": f"PT-{symbol}", "price": 0.95},
        "yt": {"symbol": f"YT-{symbol}", "price": 0.05},
    }


def _page(results: list[dict], total: int) -> dict:
    return {"results": results, "total": total}


PENDLE_ETH_URL = re.compile(r"https://api-v2\.pendle\.finance/core/v1/1/markets.*")
PENDLE_ARB_URL = re.compile(r"https://api-v2\.pendle\.finance/core/v1/42161/markets.*")
PENDLE_BSC_URL = re.compile(r"https://api-v2\.pendle\.finance/core/v1/56/markets.*")


@pytest.fixture
def adapter() -> PendleAdapter:
    return PendleAdapter()


# ---------------------------------------------------------------------------
# PT opportunity tests
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_pt_opportunity_type(adapter: PendleAdapter):
    """PT row is PENDLE_PT type, SUPPLY side, FIXED_TERM duration."""
    m = _market()
    respx.get(PENDLE_ETH_URL).mock(return_value=Response(200, json=_page([m], 1)))
    respx.get(PENDLE_ARB_URL).mock(return_value=Response(200, json=_page([], 0)))
    respx.get(PENDLE_BSC_URL).mock(return_value=Response(200, json=_page([], 0)))

    opps = await adapter.fetch_opportunities()
    pt = next(o for o in opps if o.opportunity_type == OpportunityType.PENDLE_PT)

    assert pt.side == OpportunitySide.SUPPLY
    assert pt.effective_duration == EffectiveDuration.FIXED_TERM


@respx.mock
@pytest.mark.asyncio
async def test_pt_apy_conversion(adapter: PendleAdapter):
    """PT APY is impliedApy × 100 (decimal fraction → percent)."""
    m = _market(implied_apy=0.053)
    respx.get(PENDLE_ETH_URL).mock(return_value=Response(200, json=_page([m], 1)))
    respx.get(PENDLE_ARB_URL).mock(return_value=Response(200, json=_page([], 0)))
    respx.get(PENDLE_BSC_URL).mock(return_value=Response(200, json=_page([], 0)))

    opps = await adapter.fetch_opportunities()
    pt = next(o for o in opps if o.opportunity_type == OpportunityType.PENDLE_PT)

    assert pt.total_apy_pct == pytest.approx(5.3)
    assert pt.base_apy_pct == pytest.approx(5.3)


@respx.mock
@pytest.mark.asyncio
async def test_pt_is_pendle_fields(adapter: PendleAdapter):
    """PT has is_pendle=True and pendle_type='PT'."""
    m = _market()
    respx.get(PENDLE_ETH_URL).mock(return_value=Response(200, json=_page([m], 1)))
    respx.get(PENDLE_ARB_URL).mock(return_value=Response(200, json=_page([], 0)))
    respx.get(PENDLE_BSC_URL).mock(return_value=Response(200, json=_page([], 0)))

    opps = await adapter.fetch_opportunities()
    pt = next(o for o in opps if o.opportunity_type == OpportunityType.PENDLE_PT)

    assert pt.is_pendle is True
    assert pt.pendle_type == "PT"


@respx.mock
@pytest.mark.asyncio
async def test_pt_maturity_fields(adapter: PendleAdapter):
    """PT has maturity_date set and days_to_maturity roughly correct."""
    m = _market(days=90)
    respx.get(PENDLE_ETH_URL).mock(return_value=Response(200, json=_page([m], 1)))
    respx.get(PENDLE_ARB_URL).mock(return_value=Response(200, json=_page([], 0)))
    respx.get(PENDLE_BSC_URL).mock(return_value=Response(200, json=_page([], 0)))

    opps = await adapter.fetch_opportunities()
    pt = next(o for o in opps if o.opportunity_type == OpportunityType.PENDLE_PT)

    assert pt.maturity_date is not None
    assert 85 <= pt.days_to_maturity <= 95


@respx.mock
@pytest.mark.asyncio
async def test_pt_receipt_token(adapter: PendleAdapter):
    """PT receipt token symbol is 'PT-{underlying}', transferable and composable."""
    m = _market(symbol="stETH")
    respx.get(PENDLE_ETH_URL).mock(return_value=Response(200, json=_page([m], 1)))
    respx.get(PENDLE_ARB_URL).mock(return_value=Response(200, json=_page([], 0)))
    respx.get(PENDLE_BSC_URL).mock(return_value=Response(200, json=_page([], 0)))

    opps = await adapter.fetch_opportunities()
    pt = next(o for o in opps if o.opportunity_type == OpportunityType.PENDLE_PT)

    r = pt.receipt_token
    assert r is not None
    assert r.receipt_token_symbol == "PT-stETH"
    assert r.is_transferable is True
    assert r.is_composable is True


@respx.mock
@pytest.mark.asyncio
async def test_pt_tags(adapter: PendleAdapter):
    """PT tags include 'pendle', 'fixed-yield', and a maturity tag."""
    m = _market()
    respx.get(PENDLE_ETH_URL).mock(return_value=Response(200, json=_page([m], 1)))
    respx.get(PENDLE_ARB_URL).mock(return_value=Response(200, json=_page([], 0)))
    respx.get(PENDLE_BSC_URL).mock(return_value=Response(200, json=_page([], 0)))

    opps = await adapter.fetch_opportunities()
    pt = next(o for o in opps if o.opportunity_type == OpportunityType.PENDLE_PT)

    assert "pendle" in pt.tags
    assert "fixed-yield" in pt.tags
    assert any(t.startswith("maturity-") for t in pt.tags)


# ---------------------------------------------------------------------------
# YT opportunity tests
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_yt_opportunity_type(adapter: PendleAdapter):
    """YT row is PENDLE_YT type, SUPPLY side, FIXED_TERM duration."""
    m = _market()
    respx.get(PENDLE_ETH_URL).mock(return_value=Response(200, json=_page([m], 1)))
    respx.get(PENDLE_ARB_URL).mock(return_value=Response(200, json=_page([], 0)))
    respx.get(PENDLE_BSC_URL).mock(return_value=Response(200, json=_page([], 0)))

    opps = await adapter.fetch_opportunities()
    yt = next(o for o in opps if o.opportunity_type == OpportunityType.PENDLE_YT)

    assert yt.side == OpportunitySide.SUPPLY
    assert yt.effective_duration == EffectiveDuration.FIXED_TERM


@respx.mock
@pytest.mark.asyncio
async def test_yt_apy_conversion(adapter: PendleAdapter):
    """YT APY is ytFloatingApy × 100."""
    m = _market(yt_apy=0.021)
    respx.get(PENDLE_ETH_URL).mock(return_value=Response(200, json=_page([m], 1)))
    respx.get(PENDLE_ARB_URL).mock(return_value=Response(200, json=_page([], 0)))
    respx.get(PENDLE_BSC_URL).mock(return_value=Response(200, json=_page([], 0)))

    opps = await adapter.fetch_opportunities()
    yt = next(o for o in opps if o.opportunity_type == OpportunityType.PENDLE_YT)

    assert yt.total_apy_pct == pytest.approx(2.1)


@respx.mock
@pytest.mark.asyncio
async def test_yt_negative_apy(adapter: PendleAdapter):
    """YT APY can be negative when market prices in declining yield."""
    m = _market(yt_apy=-0.015)
    respx.get(PENDLE_ETH_URL).mock(return_value=Response(200, json=_page([m], 1)))
    respx.get(PENDLE_ARB_URL).mock(return_value=Response(200, json=_page([], 0)))
    respx.get(PENDLE_BSC_URL).mock(return_value=Response(200, json=_page([], 0)))

    opps = await adapter.fetch_opportunities()
    yt = next(o for o in opps if o.opportunity_type == OpportunityType.PENDLE_YT)

    assert yt.total_apy_pct == pytest.approx(-1.5)


@respx.mock
@pytest.mark.asyncio
async def test_yt_is_pendle_fields(adapter: PendleAdapter):
    """YT has is_pendle=True and pendle_type='YT'."""
    m = _market()
    respx.get(PENDLE_ETH_URL).mock(return_value=Response(200, json=_page([m], 1)))
    respx.get(PENDLE_ARB_URL).mock(return_value=Response(200, json=_page([], 0)))
    respx.get(PENDLE_BSC_URL).mock(return_value=Response(200, json=_page([], 0)))

    opps = await adapter.fetch_opportunities()
    yt = next(o for o in opps if o.opportunity_type == OpportunityType.PENDLE_YT)

    assert yt.is_pendle is True
    assert yt.pendle_type == "YT"


@respx.mock
@pytest.mark.asyncio
async def test_yt_tags(adapter: PendleAdapter):
    """YT tags include 'pendle', 'variable-yield', 'leveraged', maturity tag."""
    m = _market()
    respx.get(PENDLE_ETH_URL).mock(return_value=Response(200, json=_page([m], 1)))
    respx.get(PENDLE_ARB_URL).mock(return_value=Response(200, json=_page([], 0)))
    respx.get(PENDLE_BSC_URL).mock(return_value=Response(200, json=_page([], 0)))

    opps = await adapter.fetch_opportunities()
    yt = next(o for o in opps if o.opportunity_type == OpportunityType.PENDLE_YT)

    assert "pendle" in yt.tags
    assert "variable-yield" in yt.tags
    assert "leveraged" in yt.tags


@respx.mock
@pytest.mark.asyncio
async def test_yt_receipt_token(adapter: PendleAdapter):
    """YT receipt token symbol is 'YT-{underlying}'."""
    m = _market(symbol="sUSDe")
    respx.get(PENDLE_ETH_URL).mock(return_value=Response(200, json=_page([m], 1)))
    respx.get(PENDLE_ARB_URL).mock(return_value=Response(200, json=_page([], 0)))
    respx.get(PENDLE_BSC_URL).mock(return_value=Response(200, json=_page([], 0)))

    opps = await adapter.fetch_opportunities()
    yt = next(o for o in opps if o.opportunity_type == OpportunityType.PENDLE_YT)

    assert yt.receipt_token is not None
    assert yt.receipt_token.receipt_token_symbol == "YT-sUSDe"


# ---------------------------------------------------------------------------
# Per-market: two opportunities produced
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_one_market_produces_two_opps(adapter: PendleAdapter):
    """Each active market produces exactly one PT + one YT opportunity."""
    m = _market()
    respx.get(PENDLE_ETH_URL).mock(return_value=Response(200, json=_page([m], 1)))
    respx.get(PENDLE_ARB_URL).mock(return_value=Response(200, json=_page([], 0)))
    respx.get(PENDLE_BSC_URL).mock(return_value=Response(200, json=_page([], 0)))

    opps = await adapter.fetch_opportunities()
    assert len(opps) == 2
    types = {o.opportunity_type for o in opps}
    assert OpportunityType.PENDLE_PT in types
    assert OpportunityType.PENDLE_YT in types


# ---------------------------------------------------------------------------
# Filtering tests
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_expired_market_skipped(adapter: PendleAdapter):
    """Markets whose expiry is in the past are skipped (even if isActive=True)."""
    m = _market()
    m["expiry"] = _past_expiry()
    respx.get(PENDLE_ETH_URL).mock(return_value=Response(200, json=_page([m], 1)))
    respx.get(PENDLE_ARB_URL).mock(return_value=Response(200, json=_page([], 0)))
    respx.get(PENDLE_BSC_URL).mock(return_value=Response(200, json=_page([], 0)))

    opps = await adapter.fetch_opportunities()
    assert opps == []


@respx.mock
@pytest.mark.asyncio
async def test_inactive_market_skipped(adapter: PendleAdapter):
    """Markets with isActive=False are skipped."""
    m = _market(is_active=False)
    respx.get(PENDLE_ETH_URL).mock(return_value=Response(200, json=_page([m], 1)))
    respx.get(PENDLE_ARB_URL).mock(return_value=Response(200, json=_page([], 0)))
    respx.get(PENDLE_BSC_URL).mock(return_value=Response(200, json=_page([], 0)))

    opps = await adapter.fetch_opportunities()
    assert opps == []


@respx.mock
@pytest.mark.asyncio
async def test_low_liquidity_skipped(adapter: PendleAdapter):
    """Markets with liquidity.usd < $100k are skipped."""
    m = _market(liq_usd=50_000.0)
    respx.get(PENDLE_ETH_URL).mock(return_value=Response(200, json=_page([m], 1)))
    respx.get(PENDLE_ARB_URL).mock(return_value=Response(200, json=_page([], 0)))
    respx.get(PENDLE_BSC_URL).mock(return_value=Response(200, json=_page([], 0)))

    opps = await adapter.fetch_opportunities()
    assert opps == []


@respx.mock
@pytest.mark.asyncio
async def test_exactly_min_liquidity_included(adapter: PendleAdapter):
    """Markets with liquidity.usd == $100k are included."""
    m = _market(liq_usd=100_000.0)
    respx.get(PENDLE_ETH_URL).mock(return_value=Response(200, json=_page([m], 1)))
    respx.get(PENDLE_ARB_URL).mock(return_value=Response(200, json=_page([], 0)))
    respx.get(PENDLE_BSC_URL).mock(return_value=Response(200, json=_page([], 0)))

    opps = await adapter.fetch_opportunities()
    assert len(opps) == 2


@respx.mock
@pytest.mark.asyncio
async def test_missing_underlying_symbol_skipped(adapter: PendleAdapter):
    """Markets with no underlyingAsset.symbol are skipped."""
    m = _market()
    m["underlyingAsset"] = {"symbol": "", "address": "0x0"}
    respx.get(PENDLE_ETH_URL).mock(return_value=Response(200, json=_page([m], 1)))
    respx.get(PENDLE_ARB_URL).mock(return_value=Response(200, json=_page([], 0)))
    respx.get(PENDLE_BSC_URL).mock(return_value=Response(200, json=_page([], 0)))

    opps = await adapter.fetch_opportunities()
    assert opps == []


# ---------------------------------------------------------------------------
# Symbol filtering
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_symbol_filter_matches(adapter: PendleAdapter):
    """symbols=['stETH'] returns only stETH opportunities."""
    markets = [
        _market(address="0x1", symbol="stETH"),
        _market(address="0x2", symbol="sUSDe"),
    ]
    respx.get(PENDLE_ETH_URL).mock(return_value=Response(200, json=_page(markets, 2)))
    respx.get(PENDLE_ARB_URL).mock(return_value=Response(200, json=_page([], 0)))
    respx.get(PENDLE_BSC_URL).mock(return_value=Response(200, json=_page([], 0)))

    opps = await adapter.fetch_opportunities(symbols=["stETH"])
    assert all(o.asset_symbol == "stETH" for o in opps)
    assert len(opps) == 2  # PT + YT for stETH


@respx.mock
@pytest.mark.asyncio
async def test_symbol_filter_no_match(adapter: PendleAdapter):
    """symbols=['WBTC'] returns empty when no matching markets."""
    m = _market(symbol="stETH")
    respx.get(PENDLE_ETH_URL).mock(return_value=Response(200, json=_page([m], 1)))
    respx.get(PENDLE_ARB_URL).mock(return_value=Response(200, json=_page([], 0)))
    respx.get(PENDLE_BSC_URL).mock(return_value=Response(200, json=_page([], 0)))

    opps = await adapter.fetch_opportunities(symbols=["WBTC"])
    assert opps == []


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_pagination_fetches_all_pages(adapter: PendleAdapter):
    """Adapter fetches page 2 when total > page size (100)."""
    page1_markets = [_market(address=f"0x{i}", symbol="stETH") for i in range(100)]
    page2_markets = [_market(address="0xfinal", symbol="sUSDe")]

    call_count = 0

    def _side_effect(request):
        nonlocal call_count
        call_count += 1
        skip = int(request.url.params.get("skip", 0))
        if skip == 0:
            return Response(200, json=_page(page1_markets, 101))
        else:
            return Response(200, json=_page(page2_markets, 101))

    respx.get(PENDLE_ETH_URL).mock(side_effect=_side_effect)
    respx.get(PENDLE_ARB_URL).mock(return_value=Response(200, json=_page([], 0)))
    respx.get(PENDLE_BSC_URL).mock(return_value=Response(200, json=_page([], 0)))

    opps = await adapter.fetch_opportunities()
    # 100 stETH markets + 1 sUSDe market = 101 × 2 = 202 opps
    assert len(opps) == 202
    assert call_count == 2


@respx.mock
@pytest.mark.asyncio
async def test_single_page_no_second_fetch(adapter: PendleAdapter):
    """When total <= page size, only one page is fetched."""
    markets = [_market(address=f"0x{i}", symbol="stETH") for i in range(3)]
    call_count = 0

    def _side_effect(request):
        nonlocal call_count
        call_count += 1
        return Response(200, json=_page(markets, 3))

    respx.get(PENDLE_ETH_URL).mock(side_effect=_side_effect)
    respx.get(PENDLE_ARB_URL).mock(return_value=Response(200, json=_page([], 0)))
    respx.get(PENDLE_BSC_URL).mock(return_value=Response(200, json=_page([], 0)))

    opps = await adapter.fetch_opportunities()
    assert call_count == 1
    assert len(opps) == 6  # 3 markets × 2


# ---------------------------------------------------------------------------
# Chain filtering
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_chain_filter_ethereum_only(adapter: PendleAdapter):
    """chains=[ETHEREUM] only fetches from Ethereum, no ARB/BSC calls."""
    m = _market()
    eth_call_count = 0

    def _eth_side(request):
        nonlocal eth_call_count
        eth_call_count += 1
        return Response(200, json=_page([m], 1))

    respx.get(PENDLE_ETH_URL).mock(side_effect=_eth_side)

    opps = await adapter.fetch_opportunities(chains=[Chain.ETHEREUM])
    assert len(opps) == 2
    assert eth_call_count >= 1
    assert all(o.chain == "ETHEREUM" for o in opps)


@respx.mock
@pytest.mark.asyncio
async def test_chain_filter_solana_returns_empty(adapter: PendleAdapter):
    """Pendle doesn't support Solana; chains=[SOLANA] returns empty list."""
    opps = await adapter.fetch_opportunities(chains=[Chain.SOLANA])
    assert opps == []


@respx.mock
@pytest.mark.asyncio
async def test_arbitrum_chain(adapter: PendleAdapter):
    """Arbitrum market produces opportunities with chain='ARBITRUM'."""
    m = _market()
    respx.get(PENDLE_ETH_URL).mock(return_value=Response(200, json=_page([], 0)))
    respx.get(PENDLE_ARB_URL).mock(return_value=Response(200, json=_page([m], 1)))
    respx.get(PENDLE_BSC_URL).mock(return_value=Response(200, json=_page([], 0)))

    opps = await adapter.fetch_opportunities(chains=[Chain.ARBITRUM])
    assert len(opps) == 2
    assert all(o.chain == "ARBITRUM" for o in opps)


# ---------------------------------------------------------------------------
# TVL and liquidity fields
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_tvl_populated(adapter: PendleAdapter):
    """PT and YT both use market liquidity.usd as tvl_usd."""
    m = _market(liq_usd=8_500_000.0)
    respx.get(PENDLE_ETH_URL).mock(return_value=Response(200, json=_page([m], 1)))
    respx.get(PENDLE_ARB_URL).mock(return_value=Response(200, json=_page([], 0)))
    respx.get(PENDLE_BSC_URL).mock(return_value=Response(200, json=_page([], 0)))

    opps = await adapter.fetch_opportunities()
    for o in opps:
        assert o.tvl_usd == pytest.approx(8_500_000.0)


# ---------------------------------------------------------------------------
# Adapter static properties
# ---------------------------------------------------------------------------


def test_adapter_properties(adapter: PendleAdapter):
    assert adapter.venue == Venue.PENDLE
    assert adapter.protocol_name == "Pendle"
    assert adapter.protocol_slug == "pendle"
    assert adapter.refresh_interval_seconds == 600
    assert adapter.requires_api_key is False
    assert adapter.api_key_env_var is None
    assert set(adapter.supported_chains) == {Chain.ETHEREUM, Chain.ARBITRUM, Chain.BSC}


def test_is_collateral_not_eligible(adapter: PendleAdapter):
    """Pendle PT and YT are not collateral eligible."""
    # Construct a dummy opportunity via internal method to avoid HTTP
    from datetime import UTC, datetime, timedelta
    from asset_registry import Chain

    now = datetime.now(UTC)
    m = _market()
    m["expiry"] = (now + timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    opps = adapter._build_market_opportunities(m, Chain.ETHEREUM, now)
    assert all(o.is_collateral_eligible is False for o in opps)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_health_check_ok(adapter: PendleAdapter):
    """Health check returns ok when API responds with results."""
    respx.get(re.compile(r"https://api-v2\.pendle\.finance/core/v1/1/markets.*")).mock(
        return_value=Response(200, json={"results": [_market()], "total": 1})
    )
    result = await adapter.health_check()
    assert result["status"] == "ok"
    assert result["error"] is None


@respx.mock
@pytest.mark.asyncio
async def test_health_check_degraded(adapter: PendleAdapter):
    """Health check returns degraded when API responds with empty results."""
    respx.get(re.compile(r"https://api-v2\.pendle\.finance/core/v1/1/markets.*")).mock(
        return_value=Response(200, json={"results": [], "total": 0})
    )
    result = await adapter.health_check()
    assert result["status"] == "degraded"


@respx.mock
@pytest.mark.asyncio
async def test_health_check_down(adapter: PendleAdapter):
    """Health check returns down on HTTP error."""
    respx.get(re.compile(r"https://api-v2\.pendle\.finance/core/v1/1/markets.*")).mock(
        return_value=Response(500, json={})
    )
    result = await adapter.health_check()
    assert result["status"] == "down"
    assert result["error"] is not None
