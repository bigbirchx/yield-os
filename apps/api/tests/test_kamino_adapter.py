"""
Tests for the Kamino Finance adapter (app.connectors.kamino).

Covers:
  1. Lending market SUPPLY — APY (decimal→pct), TVL, receipt token
  2. Lending market BORROW — collateral matrix, all-other-reserves logic
  3. kToken reserves — is_amm_lp=True, still modelled as lending opportunity
  4. AMM LP symbols (non-kToken) are skipped
  5. Multi-market isolation — collateral from market A not in market B
  6. Liquidity vaults (DeFiLlama) — VAULT type, is_amm_lp=True
  7. chain=SOLANA filter
  8. Adapter static properties
"""
from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.connectors.kamino import KaminoAdapter, _is_ktoken
from app.core.config import settings
from asset_registry import Chain, Venue
from opportunity_schema import OpportunitySide, OpportunityType


# ---------------------------------------------------------------------------
# Shared market fixture
# ---------------------------------------------------------------------------

_MAIN_MARKET = {
    "lendingMarket": "7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF",
    "name": "Main Market",
    "isPrimary": True,
}

_JLP_MARKET = {
    "lendingMarket": "DxXdAyU3kCjnyggvHmY5nAwg5cRbbmdyX3npfDMjjMek",
    "name": "JLP Market",
    "isPrimary": False,
}

_MARKETS_RESPONSE = [_MAIN_MARKET, _JLP_MARKET]

# Main Market reserves — SOL + USDC + kSOLJITOSOLOrca collateral kToken
_MAIN_RESERVES = [
    {
        "reserve": "d4A2prbA2whesmvHaL88BH6Ewn5N4bTSU2Ze8P6Bc4Q",
        "liquidityToken": "SOL",
        "liquidityTokenMint": "So11111111111111111111111111111111111111112",
        "maxLtv": "0.74",
        "supplyApy": "0.048184191896828876",   # 4.8184%
        "borrowApy": "0.063859871981933660",   # 6.3860%
        "totalSupply": "3155947.986",
        "totalBorrow": "2822540.090",
        "totalBorrowUsd": "219070641.49",
        "totalSupplyUsd": "244947999.90",
    },
    {
        "reserve": "D6q6wuQSrifJKZYpR1M8R4YawnLDtDsMmWM1NbBmgJ59",
        "liquidityToken": "USDC",
        "liquidityTokenMint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        "maxLtv": "0.80",
        "supplyApy": "0.031036942385670674",   # 3.1037%
        "borrowApy": "0.044955682444784095",   # 4.4956%
        "totalSupply": "172321359.19",
        "totalBorrow": "155657240.49",
        "totalBorrowUsd": "155634796.27",
        "totalSupplyUsd": "172296512.17",
    },
    # kToken — should have is_amm_lp=True
    {
        "reserve": "kSOLJITOSOLOrcaReservePubkey",
        "liquidityToken": "kSOLJITOSOLOrca",
        "liquidityTokenMint": "kSOLJITOSOLOrca_mint",
        "maxLtv": "0.60",
        "supplyApy": "0.000000",
        "borrowApy": "0.000000",
        "totalSupply": "1500",
        "totalBorrow": "0",
        "totalBorrowUsd": "0",
        "totalSupplyUsd": "500000",
    },
    # Plain AMM LP token — should be skipped entirely
    {
        "reserve": "lpReservePubkey",
        "liquidityToken": "UNI-V2 WETH/USDC LP",
        "liquidityTokenMint": "lp_mint",
        "maxLtv": "0",
        "supplyApy": "0.01",
        "borrowApy": "0.02",
        "totalSupply": "1000",
        "totalBorrow": "0",
        "totalBorrowUsd": "0",
        "totalSupplyUsd": "5000",
    },
]

# JLP Market — JLP token + USDC
_JLP_RESERVES = [
    {
        "reserve": "jlpReservePubkey",
        "liquidityToken": "JLP",
        "liquidityTokenMint": "27G8MtK7VtTcCHkpASjSDdkWWYfoqT6ggEuKidVJidD4",
        "maxLtv": "0.50",
        "supplyApy": "0.000000",
        "borrowApy": "0.000000",
        "totalSupply": "100000",
        "totalBorrow": "0",
        "totalBorrowUsd": "0",
        "totalSupplyUsd": "82000000",
    },
    {
        "reserve": "usdcInJlpReservePubkey",
        "liquidityToken": "USDC",
        "liquidityTokenMint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        "maxLtv": "0.80",
        "supplyApy": "0.045",
        "borrowApy": "0.07",
        "totalSupply": "5000000",
        "totalBorrow": "2000000",
        "totalBorrowUsd": "2000000",
        "totalSupplyUsd": "5000000",
    },
]

_DEFILLAMA_RESPONSE = {
    "data": [
        {
            "pool": "d783c8df-e2ed-44b4-8111-111111111111",
            "project": "kamino-liquidity",
            "chain": "Solana",
            "symbol": "USDS-USDC",
            "apy": 0.56,
            "tvlUsd": 25_531_660,
            "underlyingTokens": [
                "USDSwr9ApdHk5bvJKMjzff41FfuX8bSxdKcR81vTwcA",
                "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            ],
        },
        {
            "pool": "d783c8df-e2ed-44b4-8222-222222222222",
            "project": "kamino-liquidity",
            "chain": "Solana",
            "symbol": "SOL-JITOSOL",
            "apy": 3.55,
            "tvlUsd": 6_505_732,
            "underlyingTokens": [
                "So11111111111111111111111111111111111111112",
                "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn",
            ],
        },
        # Pool below TVL threshold — should be skipped
        {
            "pool": "tiny-pool-uuid",
            "project": "kamino-liquidity",
            "chain": "Solana",
            "symbol": "TINY-COIN",
            "apy": 10.0,
            "tvlUsd": 500,
            "underlyingTokens": [],
        },
        # Non-Kamino pool — ignored
        {
            "pool": "other-pool",
            "project": "aave-v3",
            "chain": "Solana",
            "symbol": "USDC",
            "apy": 5.0,
            "tvlUsd": 1_000_000,
        },
    ]
}


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

MARKETS_URL = f"{settings.kamino_api_url}/v2/kamino-market"
MAIN_RESERVES_URL = f"{settings.kamino_api_url}/kamino-market/{_MAIN_MARKET['lendingMarket']}/reserves/metrics"
JLP_RESERVES_URL = f"{settings.kamino_api_url}/kamino-market/{_JLP_MARKET['lendingMarket']}/reserves/metrics"
DEFILLAMA_URL = settings.defillama_yields_url


def _mock_all(route):
    route.get(MARKETS_URL).mock(return_value=Response(200, json=_MARKETS_RESPONSE))
    route.get(MAIN_RESERVES_URL).mock(return_value=Response(200, json=_MAIN_RESERVES))
    route.get(JLP_RESERVES_URL).mock(return_value=Response(200, json=_JLP_RESERVES))
    route.get(DEFILLAMA_URL).mock(return_value=Response(200, json=_DEFILLAMA_RESPONSE))


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter() -> KaminoAdapter:
    return KaminoAdapter()


# ---------------------------------------------------------------------------
# kToken detection unit tests
# ---------------------------------------------------------------------------


def test_ktoken_detection_positive():
    assert _is_ktoken("kSOLJITOSOLOrca") is True
    assert _is_ktoken("kSOLMSOLRaydium") is True
    assert _is_ktoken("kSOLBSOLOrca") is True
    assert _is_ktoken("kUXDUSDCOrca") is True


def test_ktoken_detection_negative():
    assert _is_ktoken("SOL") is False
    assert _is_ktoken("USDC") is False
    assert _is_ktoken("kSOL") is False          # no DEX suffix
    assert _is_ktoken("UNI-V2 WETH/USDC LP") is False


# ---------------------------------------------------------------------------
# Supply tests
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_supply_apy_decimal_to_pct(adapter: KaminoAdapter):
    """Kamino supply APY decimal fraction is multiplied ×100."""
    _mock_all(respx)
    opps = await adapter.fetch_opportunities(chains=[Chain.SOLANA])
    sol_supply = next(
        o for o in opps
        if o.side == OpportunitySide.SUPPLY
        and o.asset_id == "SOL"
        and o.opportunity_type == OpportunityType.LENDING
        and "Main Market" in (o.market_name or "")
    )
    assert sol_supply.total_apy_pct == pytest.approx(4.8184, rel=1e-3)
    assert sol_supply.base_apy_pct == pytest.approx(4.8184, rel=1e-3)


@respx.mock
@pytest.mark.asyncio
async def test_supply_tvl(adapter: KaminoAdapter):
    """TVL and total_supplied_usd populated from API totalSupplyUsd."""
    _mock_all(respx)
    opps = await adapter.fetch_opportunities(chains=[Chain.SOLANA])
    sol_supply = next(
        o for o in opps
        if o.side == OpportunitySide.SUPPLY and o.asset_id == "SOL"
        and o.opportunity_type == OpportunityType.LENDING
        and "Main Market" in (o.market_name or "")
    )
    assert sol_supply.tvl_usd == pytest.approx(244_947_999.90, rel=1e-3)
    assert sol_supply.total_supplied_usd == pytest.approx(244_947_999.90, rel=1e-3)


@respx.mock
@pytest.mark.asyncio
async def test_supply_liquidity_info(adapter: KaminoAdapter):
    """Utilization and available liquidity computed correctly."""
    _mock_all(respx)
    opps = await adapter.fetch_opportunities(chains=[Chain.SOLANA])
    usdc_supply = next(
        o for o in opps
        if o.side == OpportunitySide.SUPPLY and o.asset_id == "USDC"
        and "Main Market" in (o.market_name or "")
    )
    # available = 172296512 - 155634796 = 16661716
    assert usdc_supply.liquidity.available_liquidity_usd == pytest.approx(
        172296512.17 - 155634796.27, rel=1e-3
    )
    # utilization = 155634796 / 172296512 * 100
    expected_util = 155634796.27 / 172296512.17 * 100.0
    assert usdc_supply.liquidity.utilization_rate_pct == pytest.approx(expected_util, rel=1e-3)


@respx.mock
@pytest.mark.asyncio
async def test_supply_collateral_eligible(adapter: KaminoAdapter):
    """Reserves with maxLtv > 0 are collateral-eligible."""
    _mock_all(respx)
    opps = await adapter.fetch_opportunities(chains=[Chain.SOLANA])
    sol_supply = next(
        o for o in opps
        if o.side == OpportunitySide.SUPPLY and o.asset_id == "SOL"
        and "Main Market" in (o.market_name or "")
    )
    assert sol_supply.is_collateral_eligible is True
    assert sol_supply.as_collateral_max_ltv_pct == pytest.approx(74.0)


# ---------------------------------------------------------------------------
# kToken tests
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_ktoken_is_amm_lp(adapter: KaminoAdapter):
    """kToken reserves are tagged is_amm_lp=True."""
    _mock_all(respx)
    opps = await adapter.fetch_opportunities(chains=[Chain.SOLANA])
    ktoken_supply = next(
        (o for o in opps
         if "kSOLJITOSOLOrca" in (o.asset_symbol or "")),
        None,
    )
    assert ktoken_supply is not None, "kToken supply opportunity should exist"
    assert ktoken_supply.is_amm_lp is True


@respx.mock
@pytest.mark.asyncio
async def test_non_ktoken_amm_lp_skipped(adapter: KaminoAdapter):
    """Non-kToken AMM LP tokens are completely skipped."""
    _mock_all(respx)
    opps = await adapter.fetch_opportunities(chains=[Chain.SOLANA])
    lp_opps = [o for o in opps if "UNI-V2" in (o.asset_symbol or "")]
    assert len(lp_opps) == 0


# ---------------------------------------------------------------------------
# Borrow / collateral matrix tests
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_borrow_apy_decimal_to_pct(adapter: KaminoAdapter):
    """Borrow APY decimal fraction is multiplied ×100."""
    _mock_all(respx)
    opps = await adapter.fetch_opportunities(chains=[Chain.SOLANA])
    sol_borrow = next(
        o for o in opps
        if o.side == OpportunitySide.BORROW and o.asset_id == "SOL"
        and "Main Market" in (o.market_name or "")
    )
    assert sol_borrow.total_apy_pct == pytest.approx(6.3860, rel=1e-3)


@respx.mock
@pytest.mark.asyncio
async def test_borrow_collateral_excludes_self(adapter: KaminoAdapter):
    """Borrow collateral options do not include the borrowed asset itself."""
    _mock_all(respx)
    opps = await adapter.fetch_opportunities(chains=[Chain.SOLANA])
    sol_borrow = next(
        o for o in opps
        if o.side == OpportunitySide.BORROW and o.asset_id == "SOL"
        and "Main Market" in (o.market_name or "")
    )
    assert sol_borrow.collateral_options is not None
    collateral_ids = {c.asset_id for c in sol_borrow.collateral_options}
    assert "SOL" not in collateral_ids
    assert "USDC" in collateral_ids


@respx.mock
@pytest.mark.asyncio
async def test_borrow_collateral_ltv(adapter: KaminoAdapter):
    """Collateral max_ltv_pct = maxLtv × 100."""
    _mock_all(respx)
    opps = await adapter.fetch_opportunities(chains=[Chain.SOLANA])
    usdc_borrow = next(
        o for o in opps
        if o.side == OpportunitySide.BORROW and o.asset_id == "USDC"
        and "Main Market" in (o.market_name or "")
    )
    sol_col = next(c for c in usdc_borrow.collateral_options if c.asset_id == "SOL")
    assert sol_col.max_ltv_pct == pytest.approx(74.0)


# ---------------------------------------------------------------------------
# Multi-market isolation
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_cross_market_isolation(adapter: KaminoAdapter):
    """Collateral from the JLP Market does not appear in the Main Market borrow."""
    _mock_all(respx)
    opps = await adapter.fetch_opportunities(chains=[Chain.SOLANA])

    # USDC appears in both markets — find the Main Market one
    usdc_borrow_main = next(
        o for o in opps
        if o.side == OpportunitySide.BORROW and o.asset_id == "USDC"
        and "Main Market" in (o.market_name or "")
    )
    collateral_ids = {c.asset_id for c in usdc_borrow_main.collateral_options}
    # JLP is in the JLP Market only — should NOT appear as collateral in Main Market
    assert "JLP" not in collateral_ids


# ---------------------------------------------------------------------------
# Liquidity vault tests
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_liquidity_vault_is_amm_lp(adapter: KaminoAdapter):
    """Kamino Liquidity vaults are tagged is_amm_lp=True and type=VAULT."""
    _mock_all(respx)
    opps = await adapter.fetch_opportunities(chains=[Chain.SOLANA])
    vault_opps = [o for o in opps if o.opportunity_type == OpportunityType.VAULT]

    assert len(vault_opps) >= 2
    for v in vault_opps:
        assert v.is_amm_lp is True
        assert v.side == OpportunitySide.SUPPLY


@respx.mock
@pytest.mark.asyncio
async def test_liquidity_vault_apy(adapter: KaminoAdapter):
    """Liquidity vault APY from DeFiLlama (already in %) is used as-is."""
    _mock_all(respx)
    opps = await adapter.fetch_opportunities(chains=[Chain.SOLANA])
    sol_jito_vault = next(
        (o for o in opps
         if o.opportunity_type == OpportunityType.VAULT
         and "SOL-JITOSOL" in (o.asset_symbol or "")),
        None,
    )
    assert sol_jito_vault is not None
    assert sol_jito_vault.total_apy_pct == pytest.approx(3.55)


@respx.mock
@pytest.mark.asyncio
async def test_liquidity_vault_tvl_filter(adapter: KaminoAdapter):
    """Vaults with TVL < $10k are excluded."""
    _mock_all(respx)
    opps = await adapter.fetch_opportunities(chains=[Chain.SOLANA])
    vault_opps = [o for o in opps if o.opportunity_type == OpportunityType.VAULT]
    symbols = {o.asset_symbol for o in vault_opps}
    assert "TINY-COIN" not in str(symbols)


# ---------------------------------------------------------------------------
# Chain filter + adapter properties
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_non_solana_returns_empty(adapter: KaminoAdapter):
    """Non-Solana chains return no opportunities."""
    _mock_all(respx)
    opps = await adapter.fetch_opportunities(chains=[Chain.ETHEREUM])
    assert opps == []


def test_adapter_properties(adapter: KaminoAdapter):
    assert adapter.venue == Venue.KAMINO
    assert adapter.protocol_name == "Kamino"
    assert adapter.protocol_slug == "kamino"
    assert adapter.refresh_interval_seconds == 300
    assert adapter.requires_api_key is False
    assert Chain.SOLANA in adapter.supported_chains
    assert len(adapter.supported_chains) == 1


@respx.mock
@pytest.mark.asyncio
async def test_health_check_ok(adapter: KaminoAdapter):
    respx.get(MARKETS_URL).mock(return_value=Response(200, json=_MARKETS_RESPONSE))
    result = await adapter.health_check()
    assert result["status"] == "ok"
    assert result["error"] is None
