"""
Tests for the dated futures basis service and router.

All external HTTP calls and the reference-repo Deribit functions are mocked
so tests run without live API access or path injection.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_snap_cache():
    """Ensure snapshot cache is empty before / after each test."""
    from app.services import basis_service
    basis_service._snap_cache.clear()
    yield
    basis_service._snap_cache.clear()


def _make_app():
    from fastapi import FastAPI
    from app.routers.basis import router
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client():
    return TestClient(_make_app())


# ---------------------------------------------------------------------------
# Unit tests — helper functions
# ---------------------------------------------------------------------------

def test_fmt_contract():
    from app.services.basis_service import _fmt_contract
    dt = datetime(2025, 3, 28, 8, tzinfo=UTC)
    assert _fmt_contract("BTC", dt) == "BTC-28MAR25"


def test_parse_contract_expiry():
    from app.services.basis_service import _parse_contract_expiry
    result = _parse_contract_expiry("BTC-28MAR25")
    assert result is not None
    assert result.year == 2025
    assert result.month == 3
    assert result.day == 28
    assert result.tzinfo == UTC


def test_parse_contract_expiry_invalid():
    from app.services.basis_service import _parse_contract_expiry
    assert _parse_contract_expiry("INVALID") is None
    assert _parse_contract_expiry("") is None


def test_basis_ann_formula():
    from app.services.basis_service import _basis_ann
    # 1000 USD basis on 84000 index over 90 days
    result = _basis_ann(1000.0, 84000.0, 90)
    expected = (1000.0 / 84000.0) * (365 / 90)
    assert result is not None
    assert abs(result - expected) < 1e-10


def test_basis_ann_zero_dte():
    from app.services.basis_service import _basis_ann
    assert _basis_ann(1000.0, 84000.0, 0) is None


def test_third_friday():
    from app.services.basis_service import _third_friday
    # March 2025: 3rd Friday is March 21
    dt = _third_friday(2025, 3)
    assert dt.weekday() == 4  # Friday
    assert dt.day == 21
    assert dt.month == 3
    assert dt.year == 2025


def test_next_cme_expiries_count():
    from app.services.basis_service import _next_cme_expiries
    expiries = _next_cme_expiries(4)
    assert len(expiries) == 4
    # All in the future
    now = datetime.now(UTC)
    for _, dt in expiries:
        assert dt > now


def test_safe_float():
    from app.services.basis_service import _safe_float
    assert _safe_float("85000.5") == 85000.5
    assert _safe_float(None) is None
    assert _safe_float("bad") is None
    assert _safe_float(0) == 0.0


# ---------------------------------------------------------------------------
# Unit tests — Binance snapshot fetcher
# ---------------------------------------------------------------------------

def _make_httpx_mock(responses: dict) -> MagicMock:
    """Build a mock httpx.AsyncClient where each URL returns its payload."""
    mock_client = AsyncMock()

    async def _get(url, **kwargs):
        # Match by URL substring
        for key, payload in responses.items():
            if key in url:
                resp = MagicMock()
                resp.status_code = 200
                resp.raise_for_status = MagicMock()
                resp.json = MagicMock(return_value=payload)
                return resp
        resp = MagicMock()
        resp.status_code = 404
        resp.raise_for_status = MagicMock(side_effect=Exception("404"))
        resp.json = MagicMock(return_value={})
        return resp

    mock_client.get = _get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


@pytest.mark.asyncio
async def test_binance_snapshot_basic():
    from app.services.basis_service import _fetch_binance_snapshot

    expiry_ms = int(datetime(2027, 6, 27, 8, tzinfo=UTC).timestamp() * 1000)
    exchange_info = {
        "symbols": [
            {
                "symbol": "BTCUSDT_270627",
                "baseAsset": "BTC",
                "contractType": "CURRENT_QUARTER",
                "status": "TRADING",
                "deliveryDate": expiry_ms,
            }
        ]
    }
    premium_index = {"markPrice": "85500.00", "indexPrice": "85000.00"}
    open_interest = {"openInterest": "12500.0"}
    ticker_24hr = {"quoteVolume": "450000000.0"}

    mock_client = AsyncMock()
    call_count = 0

    async def _get(url, **kwargs):
        nonlocal call_count
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        if "exchangeInfo" in url:
            resp.json = MagicMock(return_value=exchange_info)
        elif "premiumIndex" in url:
            resp.json = MagicMock(return_value=premium_index)
        elif "openInterest" in url:
            resp.json = MagicMock(return_value=open_interest)
        elif "ticker/24hr" in url:
            resp.json = MagicMock(return_value=ticker_24hr)
        else:
            resp.json = MagicMock(return_value={})
        return resp

    mock_client.get = _get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.basis_service.httpx.AsyncClient", return_value=mock_client):
        rows = await _fetch_binance_snapshot("BTC")

    assert len(rows) == 1
    row = rows[0]
    assert row.venue == "binance"
    assert "BTC-" in row.contract
    assert row.futures_price == 85500.0
    assert row.index_price == 85000.0
    assert row.basis_usd == 500.0
    assert row.basis_pct_ann is not None
    assert row.basis_pct_ann > 0
    assert row.volume_24h_usd == 450000000.0


@pytest.mark.asyncio
async def test_binance_snapshot_filters_expired():
    """Contracts with DTE <= 0 must be excluded."""
    from app.services.basis_service import _fetch_binance_snapshot

    past_ms = int(datetime(2020, 1, 1, tzinfo=UTC).timestamp() * 1000)
    exchange_info = {
        "symbols": [
            {
                "symbol": "BTCUSDT_200101",
                "baseAsset": "BTC",
                "contractType": "CURRENT_QUARTER",
                "status": "TRADING",
                "deliveryDate": past_ms,
            }
        ]
    }
    mock_client = AsyncMock()

    async def _get(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=exchange_info)
        return resp

    mock_client.get = _get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.basis_service.httpx.AsyncClient", return_value=mock_client):
        rows = await _fetch_binance_snapshot("BTC")

    assert rows == []


# ---------------------------------------------------------------------------
# Unit tests — OKX snapshot fetcher
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_okx_snapshot_basic():
    from app.services.basis_service import _fetch_okx_snapshot

    expiry_ms = int(datetime(2027, 6, 27, 8, tzinfo=UTC).timestamp() * 1000)
    instruments = {
        "data": [
            {
                "instId": "BTC-USDT-270627",
                "baseCcy": "BTC",
                "quoteCcy": "USDT",
                "ctType": "linear",
                "state": "live",
                "expTime": str(expiry_ms),
                "ctVal": "0.01",
            }
        ]
    }
    mark_price = {"data": [{"markPx": "85500"}]}
    open_interest = {"data": [{"oi": "150000", "oiUsd": "12750000000"}]}
    ticker = {"data": [{"idxPx": "85000", "volCcy24h": "5000"}]}

    call_responses = {
        "instruments": instruments,
        "mark-price": mark_price,
        "open-interest": open_interest,
        "market/ticker": ticker,
    }

    mock_client = AsyncMock()

    async def _get(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        for key, payload in call_responses.items():
            if key in url:
                resp.json = MagicMock(return_value=payload)
                return resp
        resp.json = MagicMock(return_value={})
        return resp

    mock_client.get = _get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.basis_service.httpx.AsyncClient", return_value=mock_client):
        rows = await _fetch_okx_snapshot("BTC")

    assert len(rows) == 1
    row = rows[0]
    assert row.venue == "okx"
    assert row.futures_price == 85500.0
    assert row.index_price == 85000.0
    assert row.basis_usd == 500.0


# ---------------------------------------------------------------------------
# Unit tests — Bybit snapshot fetcher
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bybit_snapshot_basic():
    from app.services.basis_service import _fetch_bybit_snapshot

    expiry_ms = int(datetime(2027, 6, 27, 8, tzinfo=UTC).timestamp() * 1000)
    instruments = {
        "result": {
            "list": [
                {
                    "symbol": "BTCUSDT-27JUN25",
                    "baseCoin": "BTC",
                    "contractType": "LinearFutures",
                    "status": "Trading",
                    "deliveryTime": str(expiry_ms),
                }
            ]
        }
    }
    ticker = {
        "result": {
            "list": [
                {
                    "markPrice": "85500",
                    "indexPrice": "85000",
                    "openInterest": "12000",
                    "openInterestValue": "1020000000",
                    "turnover24h": "450000000",
                }
            ]
        }
    }

    mock_client = AsyncMock()

    async def _get(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        if "instruments-info" in url:
            resp.json = MagicMock(return_value=instruments)
        elif "tickers" in url:
            resp.json = MagicMock(return_value=ticker)
        else:
            resp.json = MagicMock(return_value={})
        return resp

    mock_client.get = _get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.basis_service.httpx.AsyncClient", return_value=mock_client):
        rows = await _fetch_bybit_snapshot("BTC")

    assert len(rows) == 1
    row = rows[0]
    assert row.venue == "bybit"
    assert row.futures_price == 85500.0
    assert row.index_price == 85000.0


# ---------------------------------------------------------------------------
# Unit tests — CME snapshot (skips gracefully without key)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cme_snapshot_skips_without_key():
    from app.services.basis_service import _fetch_cme_snapshot

    with patch("app.services.basis_service._HAS_DERIBIT", False):
        rows = await _fetch_cme_snapshot("BTC")
    assert rows == []


@pytest.mark.asyncio
async def test_cme_snapshot_skips_on_403():
    """CME returns 403 subscription error → empty list, no exception raised."""
    from app.services.basis_service import _fetch_cme_snapshot

    spot_resp = MagicMock()
    spot_resp.status_code = 200
    spot_resp.json = MagicMock(return_value={"price": "85000"})

    cme_resp = MagicMock()
    cme_resp.status_code = 403
    cme_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    call_idx = [0]

    async def _get(url, **kwargs):
        if "binance.com" in url:
            return spot_resp
        return cme_resp

    mock_client.get = _get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("app.services.basis_service._HAS_DERIBIT", True),
        patch("app.services.basis_service._AD_DERIVS_KEY", "test_key"),
        patch("app.services.basis_service.httpx.AsyncClient", return_value=mock_client),
    ):
        rows = await _fetch_cme_snapshot("BTC")

    assert rows == []


# ---------------------------------------------------------------------------
# Unit tests — Binance history
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_binance_history_basic():
    from app.services.basis_service import _binance_history

    now_ms = int(datetime(2025, 3, 1, tzinfo=UTC).timestamp() * 1000)
    klines = [[now_ms, "84000", "85000", "83500", "84500", "1000", now_ms + 86400000]]
    premium = {"indexPrice": "84000"}

    mock_client = AsyncMock()

    async def _get(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        if "klines" in url:
            resp.json = MagicMock(return_value=klines)
        elif "premiumIndex" in url:
            resp.json = MagicMock(return_value=premium)
        else:
            resp.json = MagicMock(return_value={})
        return resp

    mock_client.get = _get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.basis_service.httpx.AsyncClient", return_value=mock_client):
        result = await _binance_history("BTC", "BTC-27JUN27", 30)

    assert len(result) == 1
    pt = result[0]
    assert pt.futures_price == 84500.0
    assert pt.index_price == 84000.0
    assert pt.basis_usd == 500.0


# ---------------------------------------------------------------------------
# Integration — router endpoints
# ---------------------------------------------------------------------------

def test_snapshot_endpoint_empty(client):
    """Snapshot endpoint returns 200 with an empty term_structure when all
    fetchers are mocked to return []."""
    with patch(
        "app.routers.basis.get_basis_snapshot",
        new=AsyncMock(return_value=[]),
    ):
        resp = client.get("/api/basis/snapshot?symbol=BTC")
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "BTC"
    assert body["term_structure"] == []


def test_snapshot_endpoint_with_data(client):
    from app.services.basis_service import BasisRow

    mock_rows = [
        BasisRow(
            venue="binance",
            contract="BTC-27JUN27",
            expiry="2025-03-28T08:00:00+00:00",
            days_to_expiry=14,
            futures_price=85000.0,
            index_price=84000.0,
            basis_usd=1000.0,
            basis_pct_ann=0.158,
            oi_coin=12500.0,
            oi_usd=1050000000.0,
            volume_24h_usd=450000000.0,
        )
    ]

    with patch(
        "app.routers.basis.get_basis_snapshot",
        new=AsyncMock(return_value=mock_rows),
    ):
        resp = client.get("/api/basis/snapshot?symbol=BTC")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["term_structure"]) == 1
    row = body["term_structure"][0]
    assert row["venue"] == "binance"
    assert row["contract"] == "BTC-27JUN27"
    assert row["basis_usd"] == 1000.0
    assert abs(row["basis_pct_ann"] - 0.158) < 1e-6


def test_history_endpoint(client):
    from app.services.basis_service import BasisHistoryPoint

    mock_series = [
        BasisHistoryPoint(
            timestamp="2025-03-01T00:00:00+00:00",
            basis_usd=800.0,
            basis_pct_ann=0.12,
            futures_price=84800.0,
            index_price=84000.0,
            days_to_expiry=27.0,
        )
    ]

    with patch(
        "app.routers.basis.get_basis_history",
        new=AsyncMock(return_value=mock_series),
    ):
        resp = client.get(
            "/api/basis/history?symbol=BTC&venue=binance&contract=BTC-28MAR25&days=30"
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["venue"] == "binance"
    assert body["contract"] == "BTC-28MAR25"
    assert len(body["series"]) == 1
    assert body["series"][0]["basis_usd"] == 800.0


def test_history_endpoint_invalid_venue(client):
    with patch(
        "app.routers.basis.get_basis_history",
        new=AsyncMock(return_value=[]),
    ):
        resp = client.get(
            "/api/basis/history?symbol=BTC&venue=nonexistent&contract=BTC-28MAR25&days=30"
        )
    assert resp.status_code == 200
    assert resp.json()["series"] == []
