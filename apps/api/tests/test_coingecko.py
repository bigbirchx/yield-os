"""
Tests for the CoinGecko reference layer.

All HTTP calls are mocked via httpx.AsyncClient patching — no live API calls.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(UTC)


def _make_http_mock(json_body: object, status: int = 200):
    """Return an AsyncClient mock that returns the given body."""
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
    mock_client.get.return_value = mock_resp
    return mock_client


# ---------------------------------------------------------------------------
# CoinGeckoClient unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ping_success():
    with patch(
        "app.connectors.coingecko_client.httpx.AsyncClient",
        return_value=_make_http_mock({"gecko_says": "(V3) To the Moon!"}),
    ):
        from app.connectors.coingecko_client import CoinGeckoClient
        client = CoinGeckoClient()
        assert await client.ping() is True


@pytest.mark.asyncio
async def test_ping_failure():
    err_client = AsyncMock()
    err_client.__aenter__.return_value = err_client
    err_client.__aexit__.return_value = None
    err_client.get.side_effect = Exception("network error")
    with patch("app.connectors.coingecko_client.httpx.AsyncClient", return_value=err_client):
        from app.connectors.coingecko_client import CoinGeckoClient
        client = CoinGeckoClient()
        assert await client.ping() is False


@pytest.mark.asyncio
async def test_coins_markets_returns_list():
    payload = [
        {
            "id": "bitcoin",
            "symbol": "btc",
            "current_price": 65000.0,
            "market_cap": 1.28e12,
            "total_volume": 2.5e10,
            "price_change_percentage_24h": 1.5,
            "circulating_supply": 19700000.0,
            "total_supply": 21000000.0,
            "max_supply": 21000000.0,
            "fully_diluted_valuation": 1.37e12,
        }
    ]
    with patch(
        "app.connectors.coingecko_client.httpx.AsyncClient",
        return_value=_make_http_mock(payload),
    ):
        from app.connectors.coingecko_client import CoinGeckoClient
        client = CoinGeckoClient()
        result = await client.coins_markets(ids=["bitcoin"])

    assert len(result) == 1
    assert result[0]["id"] == "bitcoin"
    assert result[0]["current_price"] == pytest.approx(65000.0)


@pytest.mark.asyncio
async def test_coins_markets_returns_empty_on_error():
    err_client = AsyncMock()
    err_client.__aenter__.return_value = err_client
    err_client.__aexit__.return_value = None
    err_client.get.side_effect = Exception("API down")
    with patch("app.connectors.coingecko_client.httpx.AsyncClient", return_value=err_client):
        from app.connectors.coingecko_client import CoinGeckoClient
        client = CoinGeckoClient()
        result = await client.coins_markets(ids=["bitcoin"])
    assert result == []


@pytest.mark.asyncio
async def test_global_data_extracts_data_key():
    payload = {
        "data": {
            "total_market_cap": {"usd": 2.5e12},
            "total_volume": {"usd": 1e11},
            "market_cap_percentage": {"btc": 52.3, "eth": 17.1},
            "market_cap_change_percentage_24h_usd": 0.85,
            "active_cryptocurrencies": 14000,
        }
    }
    with patch(
        "app.connectors.coingecko_client.httpx.AsyncClient",
        return_value=_make_http_mock(payload),
    ):
        from app.connectors.coingecko_client import CoinGeckoClient
        client = CoinGeckoClient()
        result = await client.global_data()

    assert result["total_market_cap"]["usd"] == pytest.approx(2.5e12)
    assert result["market_cap_percentage"]["btc"] == pytest.approx(52.3)


@pytest.mark.asyncio
async def test_api_key_info_returns_empty_when_no_key():
    with patch("app.connectors.coingecko_client.settings") as mock_settings:
        mock_settings.coingecko_api_key = ""
        from app.connectors.coingecko_client import CoinGeckoClient
        client = CoinGeckoClient()
        result = await client.api_key_info()
    assert result == {}


@pytest.mark.asyncio
async def test_market_chart_returns_empty_on_error():
    err_client = AsyncMock()
    err_client.__aenter__.return_value = err_client
    err_client.__aexit__.return_value = None
    err_client.get.side_effect = Exception("timeout")
    with patch("app.connectors.coingecko_client.httpx.AsyncClient", return_value=err_client):
        from app.connectors.coingecko_client import CoinGeckoClient
        client = CoinGeckoClient()
        result = await client.market_chart("bitcoin", days=30)
    assert result == {}


# ---------------------------------------------------------------------------
# Ingestion service tests
# ---------------------------------------------------------------------------


def _make_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.execute = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_ingest_market_snapshots_inserts_rows():
    market_payload = [
        {
            "id": "bitcoin",
            "symbol": "btc",
            "current_price": 65000.0,
            "market_cap": 1.28e12,
            "total_volume": 2.5e10,
            "price_change_percentage_24h": 1.5,
            "circulating_supply": 19700000.0,
            "total_supply": 21000000.0,
            "max_supply": 21000000.0,
            "fully_diluted_valuation": 1.37e12,
        },
        {
            "id": "ethereum",
            "symbol": "eth",
            "current_price": 3500.0,
            "market_cap": 4.2e11,
            "total_volume": 1.5e10,
            "price_change_percentage_24h": 2.1,
            "circulating_supply": 120_000_000.0,
            "total_supply": None,
            "max_supply": None,
            "fully_diluted_valuation": None,
        },
    ]
    mock_client = MagicMock()
    mock_client.coins_markets = AsyncMock(return_value=market_payload)

    with patch("app.services.coingecko_ingestion.get_client", return_value=mock_client):
        from app.services.coingecko_ingestion import ingest_market_snapshots
        db = _make_db()
        count = await ingest_market_snapshots(db)

    assert count == 2
    assert db.add.call_count == 2
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_ingest_market_snapshots_empty_response():
    mock_client = MagicMock()
    mock_client.coins_markets = AsyncMock(return_value=[])
    with patch("app.services.coingecko_ingestion.get_client", return_value=mock_client):
        from app.services.coingecko_ingestion import ingest_market_snapshots
        db = _make_db()
        count = await ingest_market_snapshots(db)
    assert count == 0
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_backfill_history_inserts_rows():
    chart_data = {
        "prices": [
            [1700000000000, 35000.0],
            [1700086400000, 35500.0],
            [1700172800000, 36000.0],
        ],
        "market_caps": [
            [1700000000000, 6.8e11],
            [1700086400000, 6.9e11],
            [1700172800000, 7.0e11],
        ],
        "total_volumes": [
            [1700000000000, 2.0e10],
            [1700086400000, 2.1e10],
            [1700172800000, 2.2e10],
        ],
    }
    mock_client = MagicMock()
    mock_client.market_chart = AsyncMock(return_value=chart_data)

    db = _make_db()
    # Simulate no existing rows in DB
    mock_result = MagicMock()
    mock_result.fetchall.return_value = []
    db.execute.return_value = mock_result

    with patch("app.services.coingecko_ingestion.get_client", return_value=mock_client):
        from app.services.coingecko_ingestion import backfill_history
        inserted = await backfill_history(db, "bitcoin", days=30)

    assert inserted == 3
    assert db.add.call_count == 3
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_backfill_history_skips_existing():
    chart_data = {
        "prices": [[1700000000000, 35000.0]],
        "market_caps": [[1700000000000, 6.8e11]],
        "total_volumes": [[1700000000000, 2.0e10]],
    }
    mock_client = MagicMock()
    mock_client.market_chart = AsyncMock(return_value=chart_data)

    db = _make_db()
    # Simulate the timestamp already existing
    existing_ts = datetime.fromtimestamp(1700000000000 / 1000, tz=UTC)
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [(existing_ts,)]
    db.execute.return_value = mock_result

    with patch("app.services.coingecko_ingestion.get_client", return_value=mock_client):
        from app.services.coingecko_ingestion import backfill_history
        inserted = await backfill_history(db, "bitcoin", days=30)

    assert inserted == 0
    db.add.assert_not_called()


# ---------------------------------------------------------------------------
# Reference router integration tests
# ---------------------------------------------------------------------------


def test_reference_assets_endpoint():
    from app.models.reference import MarketReferenceSnapshot

    snap = MarketReferenceSnapshot(
        id=1,
        snapshot_at=_NOW,
        coingecko_id="bitcoin",
        symbol="BTC",
        current_price_usd=65000.0,
        market_cap_usd=1.28e12,
        fully_diluted_valuation_usd=1.37e12,
        volume_24h_usd=2.5e10,
        circulating_supply=19700000.0,
        total_supply=21000000.0,
        max_supply=21000000.0,
        price_change_24h_pct=1.5,
        source_name="coingecko",
        raw_payload=None,
    )

    with patch(
        "app.routers.reference.get_latest_snapshots",
        new=AsyncMock(return_value=[snap]),
    ):
        from app.main import app
        client = TestClient(app)
        resp = client.get("/api/reference/assets")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["symbol"] == "BTC"
    assert data[0]["current_price_usd"] == pytest.approx(65000.0)


def test_reference_global_endpoint():
    global_payload = {
        "total_market_cap": {"usd": 2.5e12},
        "total_volume": {"usd": 1e11},
        "market_cap_percentage": {"btc": 52.3, "eth": 17.1},
        "market_cap_change_percentage_24h_usd": 0.85,
        "active_cryptocurrencies": 14000,
    }

    mock_cg = MagicMock()
    mock_cg.global_data = AsyncMock(return_value=global_payload)

    with patch("app.routers.reference.get_client", return_value=mock_cg):
        from app.main import app
        client = TestClient(app)
        resp = client.get("/api/reference/global")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total_market_cap_usd"] == pytest.approx(2.5e12)
    assert data["btc_dominance_pct"] == pytest.approx(52.3)
    assert data["source_name"] == "coingecko"


def test_reference_usage_returns_empty_when_no_data():
    with patch(
        "app.routers.reference.get_latest_api_usage",
        new=AsyncMock(return_value=None),
    ):
        from app.main import app
        client = TestClient(app)
        resp = client.get("/api/reference/usage")

    assert resp.status_code == 200
    data = resp.json()
    assert data["provider"] == "coingecko"
    assert data["remaining_credits"] is None
