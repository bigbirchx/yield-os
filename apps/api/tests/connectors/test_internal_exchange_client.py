"""
Tests for the internal exchange client.

All calls to the production library functions are mocked via unittest.mock so
no live exchange or MongoDB connections are made during the test suite.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_funding_df(exchange: str = "binance") -> pd.DataFrame:
    """Return a minimal DataFrame shaped like get_annualized_funding_rate_history output."""
    idx = pd.date_range(end=datetime.now(UTC), periods=5, freq="D", tz="UTC")
    return pd.DataFrame({exchange: [0.10, 0.12, 0.11, 0.13, 0.09]}, index=idx)


def _make_ohlc_df() -> pd.DataFrame:
    ts = pd.date_range(end=datetime.now(UTC), periods=5, freq="D", tz="UTC")
    return pd.DataFrame(
        {
            "timestamp": ts,
            "open": [100.0] * 5,
            "high": [110.0] * 5,
            "low": [90.0] * 5,
            "close": [105.0] * 5,
        }
    )


def _make_rv_df() -> pd.DataFrame:
    ts = pd.date_range(end=datetime.now(UTC), periods=5, freq="D", tz="UTC")
    return pd.DataFrame(
        {
            "timestamp": ts,
            "close": [105.0] * 5,
            "log_returns": [0.01] * 5,
            "c2c_vol_7": [0.5] * 5,
            "c2c_vol_30": [0.45] * 5,
            "c2c_vol_90": [0.40] * 5,
            "parkinson_vol_7": [0.48] * 5,
            "parkinson_vol_30": [0.43] * 5,
            "parkinson_vol_90": [0.38] * 5,
        }
    )


_BINANCE_METRICS = {
    "perpetual_open_interest_USD": 5_000_000_000.0,
    "perpetual_volume_24h_USD": 1_200_000_000.0,
    "spot_volume_24h_USD": 800_000_000.0,
    "success": True,
}


# ---------------------------------------------------------------------------
# get_funding_rate_history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_funding_rate_history_returns_rows():
    raw_df = _make_funding_df("binance")

    with (
        patch(
            "app.connectors.internal.exchange_client._HAS_APIS",
            True,
        ),
        patch(
            "app.connectors.internal.exchange_client.get_annualized_funding_rate_history",
            return_value=raw_df,
        ),
    ):
        from app.connectors.internal import exchange_client

        result = await exchange_client.get_funding_rate_history("BTC", "binance", 30)

    assert not result.empty
    assert set(result.columns) >= {"snapshot_at", "symbol", "venue", "funding_rate_annualized"}
    assert (result["symbol"] == "BTC").all()
    assert (result["venue"] == "binance").all()


@pytest.mark.asyncio
async def test_get_funding_rate_history_returns_empty_when_unavailable():
    with patch("app.connectors.internal.exchange_client._HAS_APIS", False):
        from app.connectors.internal import exchange_client

        result = await exchange_client.get_funding_rate_history("BTC")

    assert result.empty


@pytest.mark.asyncio
async def test_get_funding_rate_history_returns_empty_on_exception():
    def _raise(*args, **kwargs):
        raise RuntimeError("mongo down")

    with (
        patch("app.connectors.internal.exchange_client._HAS_APIS", True),
        patch(
            "app.connectors.internal.exchange_client.get_annualized_funding_rate_history",
            side_effect=_raise,
        ),
    ):
        from app.connectors.internal import exchange_client

        result = await exchange_client.get_funding_rate_history("BTC")

    assert result.empty


# ---------------------------------------------------------------------------
# get_current_funding_rate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_current_funding_rate_binance():
    with (
        patch("app.connectors.internal.exchange_client._HAS_APIS", True),
        patch(
            "app.connectors.internal.exchange_client.get_binance_predicted_funding_rate",
            return_value=0.15,
        ),
    ):
        from app.connectors.internal import exchange_client

        rate = await exchange_client.get_current_funding_rate("BTC", "binance")

    assert rate == pytest.approx(0.15)


@pytest.mark.asyncio
async def test_get_current_funding_rate_okx():
    with (
        patch("app.connectors.internal.exchange_client._HAS_APIS", True),
        patch(
            "app.connectors.internal.exchange_client.get_okx_funding_rate",
            return_value=0.12,
        ),
    ):
        from app.connectors.internal import exchange_client

        rate = await exchange_client.get_current_funding_rate("ETH", "okx")

    assert rate == pytest.approx(0.12)


@pytest.mark.asyncio
async def test_get_current_funding_rate_returns_zero_when_unavailable():
    with patch("app.connectors.internal.exchange_client._HAS_APIS", False):
        from app.connectors.internal import exchange_client

        rate = await exchange_client.get_current_funding_rate("SOL", "binance")

    assert rate == 0.0


@pytest.mark.asyncio
async def test_get_current_funding_rate_returns_zero_on_exception():
    def _raise(*args, **kwargs):
        raise ConnectionError("exchange down")

    with (
        patch("app.connectors.internal.exchange_client._HAS_APIS", True),
        patch(
            "app.connectors.internal.exchange_client.get_binance_predicted_funding_rate",
            side_effect=_raise,
        ),
    ):
        from app.connectors.internal import exchange_client

        rate = await exchange_client.get_current_funding_rate("BTC", "binance")

    assert rate == 0.0


# ---------------------------------------------------------------------------
# get_perp_mark_price_ohlc
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_perp_mark_price_ohlc_returns_df():
    ohlc = _make_ohlc_df()

    with (
        patch("app.connectors.internal.exchange_client._HAS_APIS", True),
        patch(
            "app.connectors.internal.exchange_client.get_perp_mark_price_ohlc",
            return_value=ohlc,
        ),
    ):
        from app.connectors.internal import exchange_client

        result = await exchange_client.get_perp_mark_price_ohlc("BTC", days_lookback=30)

    assert not result.empty
    assert {"timestamp", "open", "high", "low", "close"}.issubset(result.columns)


@pytest.mark.asyncio
async def test_get_perp_mark_price_ohlc_empty_when_unavailable():
    with patch("app.connectors.internal.exchange_client._HAS_APIS", False):
        from app.connectors.internal import exchange_client

        result = await exchange_client.get_perp_mark_price_ohlc("ETH")

    assert result.empty


# ---------------------------------------------------------------------------
# get_realized_vol
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_realized_vol_returns_df():
    rv_df = _make_rv_df()

    with (
        patch("app.connectors.internal.exchange_client._HAS_APIS", True),
        patch(
            "app.connectors.internal.exchange_client.get_rv",
            return_value=rv_df,
        ),
    ):
        from app.connectors.internal import exchange_client

        result = await exchange_client.get_realized_vol("BTC", [7, 30, 90])

    assert not result.empty
    assert "c2c_vol_7" in result.columns


@pytest.mark.asyncio
async def test_get_realized_vol_empty_when_unavailable():
    with patch("app.connectors.internal.exchange_client._HAS_APIS", False):
        from app.connectors.internal import exchange_client

        result = await exchange_client.get_realized_vol("BTC")

    assert result.empty


# ---------------------------------------------------------------------------
# get_market_metrics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_market_metrics_binance():
    with (
        patch("app.connectors.internal.exchange_client._HAS_APIS", True),
        patch(
            "app.connectors.internal.exchange_client.get_binance_market_metrics",
            return_value=_BINANCE_METRICS,
        ),
    ):
        from app.connectors.internal import exchange_client

        metrics = await exchange_client.get_market_metrics("BTC", "binance")

    assert metrics["perpetual_open_interest_USD"] == pytest.approx(5e9)
    assert metrics["success"] is True


@pytest.mark.asyncio
async def test_get_market_metrics_returns_empty_dict_when_unavailable():
    with patch("app.connectors.internal.exchange_client._HAS_APIS", False):
        from app.connectors.internal import exchange_client

        metrics = await exchange_client.get_market_metrics("BTC", "binance")

    assert metrics == {}


# ---------------------------------------------------------------------------
# internal_ingestion.ingest_all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_all_skips_when_unavailable():
    """ingest_all should return empty dict and not touch the DB."""
    with patch("app.services.internal_ingestion._HAS_APIS", False):
        from app.services.internal_ingestion import ingest_all

        # db.add is sync; db.commit is async
        mock_db = MagicMock()
        mock_db.commit = AsyncMock()
        result = await ingest_all(mock_db)

    assert result == {}
    mock_db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_ingest_all_persists_rows():
    """ingest_all should add rows for each (coin, exchange) pair and commit."""
    with (
        patch("app.services.internal_ingestion._HAS_APIS", True),
        patch(
            "app.services.internal_ingestion.exchange_client.get_current_funding_rate",
            new=AsyncMock(return_value=0.10),
        ),
        patch(
            "app.services.internal_ingestion.exchange_client.get_market_metrics",
            new=AsyncMock(return_value=_BINANCE_METRICS),
        ),
    ):
        from app.services.internal_ingestion import TRACKED_COINS, TRACKED_EXCHANGES, ingest_all

        mock_db = MagicMock()
        mock_db.commit = AsyncMock()
        result = await ingest_all(mock_db)

    expected_total = len(TRACKED_COINS) * len(TRACKED_EXCHANGES)
    assert mock_db.add.call_count == expected_total
    mock_db.commit.assert_called_once()
    assert sum(result.values()) > 0
