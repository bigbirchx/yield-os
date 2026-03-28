"""
Tests for the protocol-native borrow-rate pipeline.

Covers: connector normalization, ingestion helpers, API endpoint registration.
No live API calls; all HTTP is mocked.
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_post_mock(json_body: object, status: int = 200):
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    if status >= 400:
        import httpx
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=MagicMock(status_code=status)
        )
    mock_resp.json.return_value = json_body
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post.return_value = mock_resp
    mock_client.get.return_value = mock_resp
    return mock_client


# ---------------------------------------------------------------------------
# Aave connector normalization
# ---------------------------------------------------------------------------


_AAVE_RESERVE_RAW = {
    "underlyingToken": {"symbol": "USDC", "address": "0xA0b86991", "decimals": 6},
    "isFrozen": False,
    "isPaused": False,
    "supplyInfo": {
        "apy": {"value": "0.026"},
        "maxLTV": {"value": "0.78"},
        "liquidationThreshold": {"value": "0.80"},
        "liquidationBonus": {"value": "0.05"},
        "canBeCollateral": True,
        "supplyCap": {"usd": "1000000", "amount": {"value": "1000000"}},
        "supplyCapReached": False,
    },
    "borrowInfo": {
        "apy": {"value": "0.035"},
        "borrowCap": {"usd": "800000", "amount": {"value": "800000"}},
        "borrowCapReached": False,
        "availableLiquidity": {"usd": "200000"},
        "utilizationRate": {"value": "0.75"},
        "borrowingState": "ENABLED",
    },
}


def test_aave_reserve_parsing():
    from app.connectors.aave_client import AaveReserve

    r = AaveReserve.model_validate(_AAVE_RESERVE_RAW)
    r.market_name = "AaveV3Ethereum"
    r.market_address = "0x1234"
    r.chain_name = "Ethereum"
    r.chain_id = 1

    assert r.symbol == "USDC"
    assert r.is_active is True
    assert r.borrow_info.borrowing_enabled is True
    # Supply APY from API: 0.026 (2.6%), utilization: 0.75 (75%)
    assert abs(r.supply_info.apy.as_float - 0.026) < 1e-6
    assert abs(r.borrow_info.apy.as_float - 0.035) < 1e-6
    assert abs(r.borrow_info.utilization_rate.as_float - 0.75) < 1e-6


def test_aave_row_apy_conversion():
    """_aave_row must convert decimal fractions to percentage format."""
    from app.connectors.aave_client import AaveReserve
    from app.services.lending_rate_ingestion import _aave_row

    r = AaveReserve.model_validate(_AAVE_RESERVE_RAW)
    r.market_name = "AaveV3Ethereum"
    r.market_address = "0x1234"
    r.chain_name = "Ethereum"
    r.chain_id = 1

    now = datetime.now(UTC)
    row = _aave_row(r, now)
    assert row is not None
    assert row.symbol == "USDC"
    assert row.protocol == "aave"
    assert row.chain == "Ethereum"
    # 0.035 decimal -> 3.5% stored as 3.5
    assert abs(row.borrow_apy - 3.5) < 0.01
    # 0.026 decimal -> 2.6% stored as 2.6
    assert abs(row.supply_apy - 2.6) < 0.01
    # utilization 0.75 decimal -> stored as 0.75
    assert abs(row.utilization - 0.75) < 0.01


def test_aave_row_skips_frozen_reserve():
    from app.connectors.aave_client import AaveReserve
    from app.services.lending_rate_ingestion import _aave_row

    raw = {**_AAVE_RESERVE_RAW, "isFrozen": True}
    r = AaveReserve.model_validate(raw)
    r.market_name = "Test"
    r.market_address = "0x1234"
    r.chain_name = "Ethereum"
    r.chain_id = 1
    assert _aave_row(r, datetime.now(UTC)) is None


# ---------------------------------------------------------------------------
# Kamino connector normalization
# ---------------------------------------------------------------------------


_KAMINO_RESERVE_RAW = {
    "reserve": "FBSyPnxtHKLBZ4",
    "liquidityToken": "USDC",
    "liquidityTokenMint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "maxLtv": "0.80",
    "borrowApy": "0.04349",
    "supplyApy": "0.02883",
    "totalSupply": "5000000",
    "totalBorrow": "3000000",
    "totalBorrowUsd": "3000000.0",
    "totalSupplyUsd": "5000000.0",
}


def test_kamino_reserve_parsing():
    from app.connectors.kamino_client import KaminoReserveMetrics

    r = KaminoReserveMetrics.model_validate(_KAMINO_RESERVE_RAW)
    assert r.symbol == "USDC"
    assert r.borrow_apy == pytest.approx(0.04349, rel=1e-3)
    assert r.supply_apy == pytest.approx(0.02883, rel=1e-3)
    assert r.total_supply_usd == pytest.approx(5_000_000.0, rel=1e-3)


def test_kamino_row_apy_conversion():
    """_kamino_row must convert decimal fractions to percentage format."""
    from app.connectors.kamino_client import KaminoReserveMetrics
    from app.services.lending_rate_ingestion import _kamino_row

    r = KaminoReserveMetrics.model_validate(_KAMINO_RESERVE_RAW)
    now = datetime.now(UTC)
    row = _kamino_row(r, "market_addr", "Main Market", now)
    assert row is not None
    assert row.protocol == "kamino"
    assert row.chain == "Solana"
    # 0.04349 decimal -> 4.349% stored
    assert abs(row.borrow_apy - 4.349) < 0.01
    assert abs(row.supply_apy - 2.883) < 0.01
    # utilization = 3M / 5M = 0.6
    assert abs(row.utilization - 0.6) < 0.01


# ---------------------------------------------------------------------------
# Morpho connector normalization
# ---------------------------------------------------------------------------


_MORPHO_MARKET_RAW = {
    "uniqueKey": "0xabcd1234",
    "lltv": "860000000000000000",
    "loanAsset": {"symbol": "USDC", "address": "0xA0b86991", "decimals": 6},
    "collateralAsset": {"symbol": "WBTC", "address": "0x2260FAC5", "decimals": 8},
    "state": {
        "supplyAssets": 10000000,
        "borrowAssets": 8000000,
        "liquidityAssets": 2000000,
        "supplyAssetsUsd": 10000000.0,
        "borrowAssetsUsd": 8000000.0,
        "liquidityAssetsUsd": 2000000.0,
        "borrowApy": 0.0621,
        "supplyApy": 0.0497,
        "utilization": 0.8,
    },
}


def test_morpho_market_parsing():
    from app.connectors.morpho_client import MorphoMarket

    m = MorphoMarket.model_validate(_MORPHO_MARKET_RAW)
    assert m.loan_token.symbol == "USDC"
    assert m.collateral_token.symbol == "WBTC"
    assert abs(m.liquidation_threshold - 0.86) < 1e-6
    assert m.borrow_apy == pytest.approx(0.0621, rel=1e-3)
    assert m.supply_apy == pytest.approx(0.0497, rel=1e-3)
    assert m.utilization == pytest.approx(0.8, rel=1e-3)


def test_morpho_row_apy_conversion():
    """_morpho_row must convert decimal fractions to percentage format."""
    from app.connectors.morpho_client import MorphoMarket
    from app.services.lending_rate_ingestion import _morpho_row

    m = MorphoMarket.model_validate(_MORPHO_MARKET_RAW)
    now = datetime.now(UTC)
    row = _morpho_row(m, now)
    assert row is not None
    assert row.protocol == "morpho_blue"
    assert row.symbol == "USDC"
    assert row.chain == "Ethereum"
    assert row.market == "WBTC/USDC"
    # 0.0621 decimal -> 6.21% stored
    assert abs(row.borrow_apy - 6.21) < 0.01
    assert abs(row.supply_apy - 4.97) < 0.01
    assert row.utilization == pytest.approx(0.8, rel=1e-3)


def test_morpho_row_filters_anomalous_apy():
    """Markets with borrowApy >= 5.0 (500%) should be dropped by the property cap."""
    from app.connectors.morpho_client import MorphoMarket
    from app.services.lending_rate_ingestion import _morpho_row

    raw = {
        **_MORPHO_MARKET_RAW,
        "state": {
            **_MORPHO_MARKET_RAW["state"],
            "borrowApy": 418.89,  # anomalous value
            "utilization": 1.0,
        },
    }
    m = MorphoMarket.model_validate(raw)
    assert m.borrow_apy is None  # capped by property
    assert _morpho_row(m, datetime.now(UTC)) is None


# ---------------------------------------------------------------------------
# API endpoint registration
# ---------------------------------------------------------------------------


def test_lending_overview_endpoint_registered():
    """Ensure /api/lending/overview is registered and reachable."""
    from app.main import app

    client = TestClient(app)
    routes = [r.path for r in app.routes]
    assert "/api/lending/overview" in routes


# sync overview test removed; see test_lending_overview_returns_empty_list_with_mock_db


# ---------------------------------------------------------------------------
# Source label checks
# ---------------------------------------------------------------------------


def test_aave_row_uses_aave_protocol_label():
    from app.connectors.aave_client import AaveReserve
    from app.services.lending_rate_ingestion import _aave_row

    r = AaveReserve.model_validate(_AAVE_RESERVE_RAW)
    r.market_name = "AaveV3Ethereum"
    r.market_address = "0x1234"
    r.chain_name = "Ethereum"
    r.chain_id = 1
    row = _aave_row(r, datetime.now(UTC))
    assert row is not None
    assert row.protocol == "aave"


def test_kamino_row_uses_kamino_protocol_label():
    from app.connectors.kamino_client import KaminoReserveMetrics
    from app.services.lending_rate_ingestion import _kamino_row

    r = KaminoReserveMetrics.model_validate(_KAMINO_RESERVE_RAW)
    row = _kamino_row(r, "addr", "Main Market", datetime.now(UTC))
    assert row is not None
    assert row.protocol == "kamino"


def test_morpho_row_uses_morpho_blue_protocol_label():
    from app.connectors.morpho_client import MorphoMarket
    from app.services.lending_rate_ingestion import _morpho_row

    m = MorphoMarket.model_validate(_MORPHO_MARKET_RAW)
    row = _morpho_row(m, datetime.now(UTC))
    assert row is not None
    assert row.protocol == "morpho_blue"


