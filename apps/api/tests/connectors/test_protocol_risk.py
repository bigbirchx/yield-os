"""
Tests for Aave, Morpho, and Kamino connectors with mocked HTTP responses,
plus normalization unit tests for _from_aave, _from_morpho, _from_kamino.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import respx
from httpx import Response

from app.connectors.aave_client_legacy import AaveClient, AaveReserve
from app.connectors.morpho_client_legacy import MorphoClient, MorphoMarket
from app.connectors.kamino_client_legacy import KaminoClient, KaminoReserve
from app.services.risk_ingestion import _from_aave, _from_morpho, _from_kamino

_NOW = datetime.now(UTC)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

AAVE_SUBGRAPH_URL = "https://gateway-arbitrum.network.thegraph.com/api/test/subgraphs/id/abc"

MOCK_AAVE_RESPONSE = {
    "data": {
        "reserves": [
            {
                "id": "0xaaa-1",
                "underlyingAsset": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                "symbol": "USDC",
                "name": "USD Coin",
                "decimals": 6,
                "isActive": True,
                "isFrozen": False,
                "borrowingEnabled": True,
                "usageAsCollateralEnabled": True,
                "baseLTVasCollateral": "7700",
                "reserveLiquidationThreshold": "8000",
                "reserveLiquidationBonus": "10500",
                "borrowCap": "50000000",
                "supplyCap": "100000000",
                "availableLiquidity": "5000000000000",
                "totalCurrentVariableDebt": "3000000000000",
                "liquidityRate": "0.045",
                "variableBorrowRate": "0.065",
            },
            {
                "id": "0xbbb-2",
                "underlyingAsset": "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599",
                "symbol": "WBTC",
                "name": "Wrapped Bitcoin",
                "decimals": 8,
                "isActive": True,
                "isFrozen": False,
                "borrowingEnabled": True,
                "usageAsCollateralEnabled": True,
                "baseLTVasCollateral": "7000",
                "reserveLiquidationThreshold": "7500",
                "reserveLiquidationBonus": "10625",
                "borrowCap": "1000",
                "supplyCap": "2000",
                "availableLiquidity": "5000000000",
                "totalCurrentVariableDebt": "2000000000",
                "liquidityRate": "0.001",
                "variableBorrowRate": "0.02",
            },
        ]
    }
}

MOCK_MORPHO_RESPONSE = {
    "data": {
        "markets": [
            {
                "id": "abc123",
                "uniqueKey": "0xdeadbeef",
                "lltv": "800000000000000000",  # 80% in 1e18 format
                "loanToken": {"symbol": "USDC", "address": "0xaaa", "decimals": 6},
                "collateralToken": {"symbol": "WBTC", "address": "0xbbb", "decimals": 8},
                "state": {
                    "supplyAssets": "1000000",
                    "borrowAssets": "700000",
                    "liquidityAssets": "300000",
                    "supplyAssetsUsd": 1_000_000.0,
                    "borrowAssetsUsd": 700_000.0,
                    "liquidityAssetsUsd": 300_000.0,
                },
            }
        ]
    }
}

MOCK_KAMINO_MARKETS = [
    {"lendingMarket": "MARKET_ADDR_1", "name": "Main Market"}
]

MOCK_KAMINO_RESERVES = [
    {
        "address": "RESERVE_ADDR_1",
        "symbol": "SOL",
        "config": {
            "loanToValueRatio": 0.75,
            "liquidationThreshold": 0.80,
            "liquidationBonus": 0.05,
            "borrowLimit": 10000.0,
            "depositLimit": 20000.0,
            "status": "Active",
        },
        "liquidity": {
            "mintPubkey": "SOL_MINT",
            "availableAmount": 5000.0,
            "mintDecimals": 9,
        },
    }
]


# ---------------------------------------------------------------------------
# Aave connector tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_aave_fetch_reserves():
    respx.post(AAVE_SUBGRAPH_URL).mock(return_value=Response(200, json=MOCK_AAVE_RESPONSE))

    async with AaveClient(subgraph_url=AAVE_SUBGRAPH_URL) as client:
        reserves = await client.fetch_reserves()

    assert len(reserves) == 2
    usdc = next(r for r in reserves if r.symbol == "USDC")
    assert usdc.max_ltv == pytest.approx(0.77)
    assert usdc.liq_threshold == pytest.approx(0.80)
    assert usdc.liq_penalty == pytest.approx(0.05)
    assert usdc.borrow_cap_native == 50_000_000
    assert usdc.supply_cap_native == 100_000_000


def test_aave_normalization():
    row = _from_aave(
        AaveReserve.model_validate(MOCK_AAVE_RESPONSE["data"]["reserves"][0]),
        _NOW,
    )
    assert row.protocol == "aave-v3"
    assert row.chain == "Ethereum"
    assert row.asset == "USDC"
    assert row.max_ltv == pytest.approx(0.77)
    assert row.liquidation_threshold == pytest.approx(0.80)
    assert row.liquidation_penalty == pytest.approx(0.05)
    assert row.collateral_eligible is True
    assert row.borrowing_enabled is True
    assert row.is_active is True
    assert row.raw_payload is not None


def test_aave_no_ltv_returns_none():
    data = {**MOCK_AAVE_RESPONSE["data"]["reserves"][0], "baseLTVasCollateral": "0"}
    reserve = AaveReserve.model_validate(data)
    assert reserve.max_ltv is None


# ---------------------------------------------------------------------------
# Morpho connector tests
# ---------------------------------------------------------------------------

MORPHO_URL = "https://blue-api.morpho.org/graphql"


@pytest.mark.asyncio
@respx.mock
async def test_morpho_fetch_markets():
    respx.post(MORPHO_URL).mock(return_value=Response(200, json=MOCK_MORPHO_RESPONSE))

    async with MorphoClient(api_url=MORPHO_URL) as client:
        markets = await client.fetch_markets()

    assert len(markets) == 1
    mkt = markets[0]
    assert mkt.collateral_token.symbol == "WBTC"
    assert mkt.loan_token.symbol == "USDC"
    assert mkt.liquidation_threshold == pytest.approx(0.80)
    assert mkt.max_ltv == pytest.approx(0.76)


def test_morpho_normalization():
    market = MorphoMarket.model_validate(MOCK_MORPHO_RESPONSE["data"]["markets"][0])
    row = _from_morpho(market, _NOW)

    assert row.protocol == "morpho-blue"
    assert row.chain == "Ethereum"
    assert row.asset == "WBTC"
    assert row.debt_asset == "USDC"
    assert row.market_address == "0xdeadbeef"
    assert row.liquidation_threshold == pytest.approx(0.80)
    assert row.available_capacity_native == pytest.approx(300_000.0)
    assert row.raw_payload is not None


# ---------------------------------------------------------------------------
# Kamino connector tests
# ---------------------------------------------------------------------------

KAMINO_URL = "https://api.kamino.finance"


@pytest.mark.asyncio
@respx.mock
async def test_kamino_fetch_markets():
    respx.get(f"{KAMINO_URL}/v2/kamino-market/all").mock(
        return_value=Response(200, json=MOCK_KAMINO_MARKETS)
    )

    async with KaminoClient(base_url=KAMINO_URL) as client:
        markets = await client.fetch_markets()

    assert len(markets) == 1
    assert markets[0].lending_market == "MARKET_ADDR_1"


@pytest.mark.asyncio
@respx.mock
async def test_kamino_fetch_reserves():
    respx.get(f"{KAMINO_URL}/v2/kamino-market/MARKET_ADDR_1/reserves").mock(
        return_value=Response(200, json=MOCK_KAMINO_RESERVES)
    )

    async with KaminoClient(base_url=KAMINO_URL) as client:
        reserves = await client.fetch_reserves("MARKET_ADDR_1")

    assert len(reserves) == 1
    sol = reserves[0]
    assert sol.symbol == "SOL"
    assert sol.config is not None
    assert sol.config.loan_to_value_ratio == pytest.approx(0.75)


def test_kamino_normalization():
    reserve = KaminoReserve.model_validate(MOCK_KAMINO_RESERVES[0])
    row = _from_kamino(reserve, "MARKET_ADDR_1", _NOW)

    assert row is not None
    assert row.protocol == "kamino"
    assert row.chain == "Solana"
    assert row.asset == "SOL"
    assert row.max_ltv == pytest.approx(0.75)
    assert row.liquidation_threshold == pytest.approx(0.80)
    assert row.liquidation_penalty == pytest.approx(0.05)
    assert row.available_capacity_native == pytest.approx(5000.0)
    assert row.is_active is True
    assert row.raw_payload is not None


def test_kamino_no_symbol_returns_none():
    reserve = KaminoReserve.model_validate({**MOCK_KAMINO_RESERVES[0], "symbol": None})
    row = _from_kamino(reserve, "MARKET_ADDR_1", _NOW)
    assert row is None
