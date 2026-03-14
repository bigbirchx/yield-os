"""
Tests for the funding snapshot + history endpoints.

All external API calls (Binance, OKX, Bybit REST, Deribit REST, Coinglass)
and internal library calls are mocked — no live exchange connections are made.
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(UTC)

def _funding_df(exchange: str, rate: float = 0.10, n: int = 30) -> pd.DataFrame:
    idx = pd.date_range(end=_NOW, periods=n, freq="8h", tz="UTC")
    return pd.DataFrame(
        {"annualized_funding_rate": [rate] * n},
        index=idx.rename("snapshot_at"),
    )


# ---------------------------------------------------------------------------
# Service-layer unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_blend_equal_weighted():
    from app.services.funding_service import ExchangeData, _blend

    exchanges = {
        "binance": ExchangeData(live_apr=0.10),
        "okx": ExchangeData(live_apr=0.20),
    }
    result = _blend(exchanges)
    assert result["equal_weighted_apr"] == pytest.approx(0.15)


@pytest.mark.asyncio
async def test_blend_oi_weighted():
    from app.services.funding_service import ExchangeData, _blend

    exchanges = {
        "binance": ExchangeData(live_apr=0.10, oi_usd=3e9),
        "okx": ExchangeData(live_apr=0.30, oi_usd=1e9),
    }
    result = _blend(exchanges)
    expected = (0.10 * 3e9 + 0.30 * 1e9) / 4e9
    assert result["oi_weighted_apr"] == pytest.approx(expected)


@pytest.mark.asyncio
async def test_blend_empty_exchanges():
    from app.services.funding_service import ExchangeData, _blend

    result = _blend({"binance": ExchangeData()})
    assert result["equal_weighted_apr"] is None


@pytest.mark.asyncio
async def test_compute_ma():
    from app.services.funding_service import _compute_ma

    df = _funding_df("binance", rate=0.15, n=60)
    ma7 = _compute_ma(df, 7)
    ma30 = _compute_ma(df, 30)
    assert ma7 == pytest.approx(0.15, rel=0.01)
    assert ma30 == pytest.approx(0.15, rel=0.01)


@pytest.mark.asyncio
async def test_compute_ma_empty_df():
    from app.services.funding_service import _compute_ma

    assert _compute_ma(pd.DataFrame(), 7) is None


@pytest.mark.asyncio
async def test_funding_interval_hours():
    from app.services.funding_service import _funding_interval

    df = _funding_df("binance", n=10)
    interval = _funding_interval(df)
    assert interval == pytest.approx(8.0, abs=0.1)


@pytest.mark.asyncio
async def test_funding_interval_empty():
    from app.services.funding_service import _funding_interval

    assert _funding_interval(pd.DataFrame()) == 8.0


@pytest.mark.asyncio
async def test_norm_history_df_indexed():
    """Shape-A: indexed DataFrame with exchange-name column."""
    from app.services.funding_service import _norm_history_df

    df = _funding_df("binance", rate=0.12, n=20)
    raw = df.rename(columns={"annualized_funding_rate": "binance"})
    normed = _norm_history_df(raw, exchange_col="binance")
    assert not normed.empty
    assert "annualized_funding_rate" in normed.columns
    assert normed["annualized_funding_rate"].iloc[0] == pytest.approx(0.12)


@pytest.mark.asyncio
async def test_norm_history_df_columnar():
    """Shape-B: columnar DataFrame with timestamp column."""
    from app.services.funding_service import _norm_history_df

    idx = pd.date_range(end=_NOW, periods=10, freq="8h", tz="UTC")
    df = pd.DataFrame(
        {"timestamp": idx, "annualized_funding_rate": [0.08] * 10}
    )
    normed = _norm_history_df(df)
    assert not normed.empty
    assert normed["annualized_funding_rate"].iloc[0] == pytest.approx(0.08)


# ---------------------------------------------------------------------------
# Binance fetcher (internal API mocked)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_binance_returns_data():
    hist = _funding_df("binance", rate=0.12, n=90)

    with (
        patch("app.services.funding_service._HAS_APIS", True),
        patch(
            "app.services.funding_service.get_binance_predicted_funding_rate",
            return_value=0.14,
        ),
        patch(
            "app.services.funding_service.get_binance_market_metrics",
            return_value={
                "perpetual_open_interest_USD": 5e9,
                "perpetual_open_interest": 75_000.0,
                "perpetual_volume_24h": 120_000.0,
            },
        ),
        patch(
            "app.services.funding_service.get_annualized_funding_rate_history",
            return_value=hist.rename(columns={"annualized_funding_rate": "binance"}),
        ),
    ):
        from app.services.funding_service import _fetch_binance

        result = await _fetch_binance("BTC")

    assert result.live_apr == pytest.approx(0.14)
    assert result.oi_usd == pytest.approx(5e9)
    assert result.ma_7d_apr is not None


@pytest.mark.asyncio
async def test_fetch_binance_graceful_when_no_apis():
    with patch("app.services.funding_service._HAS_APIS", False):
        from app.services.funding_service import _fetch_binance

        result = await _fetch_binance("BTC")

    assert result.live_apr is None
    assert result.oi_usd is None


# ---------------------------------------------------------------------------
# Bybit fetcher (REST mocked via respx)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_funding_cache():
    """Ensure the module-level TTL cache doesn't bleed between tests."""
    import app.services.funding_service as svc
    svc._hist_cache.clear()
    yield
    svc._hist_cache.clear()


def _make_http_mock(json_body: dict):
    """Return an AsyncClient mock that yields `json_body` from `.get()`."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = json_body
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.get.return_value = mock_resp
    return mock_client


@pytest.mark.asyncio
async def test_fetch_bybit_live_rate():
    hist = _funding_df("bybit", rate=0.09, n=50)
    ticker_payload = {
        "result": {
            "list": [
                {
                    "fundingRate": "0.0001",
                    "openInterest": "50000",
                    "openInterestValue": "3000000000",
                }
            ]
        }
    }

    with (
        patch("app.services.funding_service._HAS_APIS", True),
        patch("app.services.funding_service.PerpFuture") as mock_pf,
        patch("app.services.funding_service.httpx.AsyncClient", return_value=_make_http_mock(ticker_payload)),
    ):
        mock_inst = MagicMock()
        mock_inst._get_bybit_funding_rate_history.return_value = hist.reset_index().rename(
            columns={"snapshot_at": "timestamp"}
        )
        mock_pf.return_value = mock_inst

        from app.services.funding_service import _fetch_bybit

        result = await _fetch_bybit("BTC")

    expected_apr = 0.0001 * 3 * 365
    assert result.live_apr == pytest.approx(expected_apr, rel=0.01)
    assert result.oi_usd == pytest.approx(3e9)


# ---------------------------------------------------------------------------
# Deribit fetcher (REST mocked)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_deribit_live_rate():
    hist = _funding_df("deribit", rate=0.08, n=50)
    ticker_payload = {"result": {"funding_8h": 0.00008}}

    with (
        patch("app.services.funding_service._HAS_APIS", True),
        patch("app.services.funding_service.PerpFuture") as mock_pf,
        patch("app.services.funding_service.httpx.AsyncClient", return_value=_make_http_mock(ticker_payload)),
    ):
        mock_inst = MagicMock()
        mock_inst._get_deribit_funding_rate_history.return_value = hist.reset_index().rename(
            columns={"snapshot_at": "timestamp"}
        )
        mock_inst._get_deribit_open_interest.return_value = 2e9
        mock_pf.return_value = mock_inst

        from app.services.funding_service import _fetch_deribit

        result = await _fetch_deribit("BTC")

    expected_apr = 0.00008 * 3 * 365
    assert result.live_apr == pytest.approx(expected_apr, rel=0.01)


# ---------------------------------------------------------------------------
# Coinglass connector
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coinglass_fetch():
    cg_payload = {
        "data": [
            {
                "symbol": "BTC",
                "uFundingRate": 0.0001,
                "oFundingRate": 0.00009,
                "bybitFundingRate": 0.00011,
            }
        ]
    }
    with patch(
        "app.connectors.coinglass_client.httpx.AsyncClient",
        return_value=_make_http_mock(cg_payload),
    ):
        from app.connectors.coinglass_client import fetch_funding_snapshot

        snap = await fetch_funding_snapshot("BTC")

    assert snap is not None
    expected_binance = 0.0001 * 3 * 365
    assert snap.binance_apr == pytest.approx(expected_binance)


@pytest.mark.asyncio
async def test_coinglass_returns_none_on_error():
    err_client = AsyncMock()
    err_client.__aenter__.return_value = err_client
    err_client.__aexit__.return_value = None
    err_client.get.side_effect = Exception("timeout")

    with patch(
        "app.connectors.coinglass_client.httpx.AsyncClient",
        return_value=err_client,
    ):
        from app.connectors.coinglass_client import fetch_funding_snapshot

        snap = await fetch_funding_snapshot("BTC")

    assert snap is None


# ---------------------------------------------------------------------------
# Full snapshot endpoint (FastAPI integration test)
# ---------------------------------------------------------------------------


def _mock_snap():
    from app.services.funding_service import ExchangeData, FundingSnapshot

    return FundingSnapshot(
        symbol="BTC",
        as_of=_NOW.isoformat(),
        exchanges={
            "binance": ExchangeData(live_apr=0.12, last_apr=0.11, funding_interval_hours=8.0, oi_usd=5e9, oi_coin=75000, volume_coin_24h=120000, ma_7d_apr=0.115, ma_30d_apr=0.118),
            "okx": ExchangeData(live_apr=0.11),
            "bybit": ExchangeData(live_apr=0.13, oi_usd=3e9),
            "deribit": ExchangeData(live_apr=0.09),
            "bullish": ExchangeData(),
        },
        blended={"equal_weighted_apr": 0.1125, "oi_weighted_apr": 0.12, "volume_weighted_apr": None},
        coinglass={"binance_apr": 0.12, "okx_apr": 0.11, "bybit_apr": 0.13},
    )


def test_snapshot_endpoint_returns_200():
    with patch(
        "app.routers.funding_snapshot.get_funding_snapshot",
        new=AsyncMock(return_value=_mock_snap()),
    ):
        from app.main import app

        client = TestClient(app)
        resp = client.get("/api/funding/snapshot?symbol=BTC")

    assert resp.status_code == 200
    data = resp.json()
    assert data["symbol"] == "BTC"
    assert "binance" in data["exchanges"]
    assert data["blended"]["equal_weighted_apr"] == pytest.approx(0.1125)
    assert data["coinglass"]["binance_apr"] == pytest.approx(0.12)


def test_history_endpoint_returns_series():
    hist = _funding_df("binance", rate=0.12, n=90)

    with patch(
        "app.routers.funding_history.get_funding_history",
        new=AsyncMock(return_value=hist),
    ):
        from app.main import app

        client = TestClient(app)
        resp = client.get("/api/funding/history?symbol=BTC&exchange=binance&days=90")

    assert resp.status_code == 200
    data = resp.json()
    assert data["symbol"] == "BTC"
    assert data["exchange"] == "binance"
    assert len(data["series"]) > 0
    assert "date" in data["series"][0]
    assert "value" in data["series"][0]


def test_history_endpoint_empty_when_no_data():
    with patch(
        "app.routers.funding_history.get_funding_history",
        new=AsyncMock(return_value=pd.DataFrame()),
    ):
        from app.main import app

        client = TestClient(app)
        resp = client.get("/api/funding/history?symbol=BTC&exchange=bullish&days=90")

    assert resp.status_code == 200
    assert resp.json()["series"] == []
