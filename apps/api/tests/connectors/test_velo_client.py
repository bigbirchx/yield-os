"""
Tests for VeloClient with mocked HTTP responses.
"""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.connectors.velo_client import VeloClient, VeloSnapshot

BASE_URL = "https://api.velo.xyz/v0"

MOCK_FUNDING = [
    {"venue": "Binance", "coin": "BTC", "funding_rate": 0.0001, "timestamp": "2026-01-01T00:00:00Z"},
    {"venue": "Bybit", "coin": "BTC", "funding_rate": 0.00015, "timestamp": "2026-01-01T00:00:00Z"},
]

MOCK_OI = [
    {"venue": "Binance", "coin": "BTC", "open_interest_usd": 5_000_000_000.0},
    {"venue": "Bybit", "coin": "BTC", "open_interest_usd": 2_000_000_000.0},
]

MOCK_SUMMARY = [
    {
        "venue": "Binance",
        "coin": "BTC",
        "mark_price": 95_000.0,
        "index_price": 94_980.0,
        "basis_annualized": 0.08,
        "spot_volume_usd": 1_200_000_000.0,
        "perp_volume_usd": 8_000_000_000.0,
    },
    {
        "venue": "Bybit",
        "coin": "BTC",
        "mark_price": 95_010.0,
        "index_price": 94_985.0,
        "basis_annualized": 0.09,
        "spot_volume_usd": 400_000_000.0,
        "perp_volume_usd": 3_000_000_000.0,
    },
]


@pytest.mark.asyncio
@respx.mock
async def test_fetch_snapshots_success():
    respx.get(f"{BASE_URL}/funding-rates").mock(return_value=Response(200, json=MOCK_FUNDING))
    respx.get(f"{BASE_URL}/open-interest").mock(return_value=Response(200, json=MOCK_OI))
    respx.get(f"{BASE_URL}/market-summary").mock(return_value=Response(200, json=MOCK_SUMMARY))

    async with VeloClient(api_key="test-key", base_url=BASE_URL) as client:
        snapshots = await client.fetch_snapshots("BTC")

    assert len(snapshots) == 2
    binance = next(s for s in snapshots if s.venue == "Binance")
    assert binance.coin == "BTC"
    assert binance.funding_rate == pytest.approx(0.0001)
    assert binance.open_interest_usd == pytest.approx(5_000_000_000.0)
    assert binance.mark_price == pytest.approx(95_000.0)
    assert binance.basis_annualized == pytest.approx(0.08)
    assert binance.raw_funding is not None
    assert binance.raw_oi is not None
    assert binance.raw_summary is not None


@pytest.mark.asyncio
@respx.mock
async def test_fetch_snapshots_venue_without_oi():
    """Venues present in funding but missing from OI should still produce a row."""
    respx.get(f"{BASE_URL}/funding-rates").mock(return_value=Response(200, json=MOCK_FUNDING))
    respx.get(f"{BASE_URL}/open-interest").mock(return_value=Response(200, json=[]))
    respx.get(f"{BASE_URL}/market-summary").mock(return_value=Response(200, json=MOCK_SUMMARY))

    async with VeloClient(api_key="test-key", base_url=BASE_URL) as client:
        snapshots = await client.fetch_snapshots("BTC")

    assert len(snapshots) == 2
    for s in snapshots:
        assert s.open_interest_usd is None


@pytest.mark.asyncio
@respx.mock
async def test_fetch_snapshots_data_envelope():
    """Velo sometimes wraps the list in {"data": [...]}."""
    respx.get(f"{BASE_URL}/funding-rates").mock(
        return_value=Response(200, json={"data": MOCK_FUNDING})
    )
    respx.get(f"{BASE_URL}/open-interest").mock(
        return_value=Response(200, json={"data": MOCK_OI})
    )
    respx.get(f"{BASE_URL}/market-summary").mock(
        return_value=Response(200, json={"data": MOCK_SUMMARY})
    )

    async with VeloClient(api_key="test-key", base_url=BASE_URL) as client:
        snapshots = await client.fetch_snapshots("BTC")

    assert len(snapshots) == 2


@pytest.mark.asyncio
async def test_client_rejects_empty_api_key():
    with pytest.raises(ValueError, match="VELO_API_KEY"):
        VeloClient(api_key="")


@pytest.mark.asyncio
@respx.mock
async def test_fetch_snapshots_http_error_propagates():
    respx.get(f"{BASE_URL}/funding-rates").mock(return_value=Response(401, json={"error": "Unauthorized"}))
    respx.get(f"{BASE_URL}/open-interest").mock(return_value=Response(200, json=MOCK_OI))
    respx.get(f"{BASE_URL}/market-summary").mock(return_value=Response(200, json=MOCK_SUMMARY))

    import httpx

    async with VeloClient(api_key="bad-key", base_url=BASE_URL) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await client.fetch_snapshots("BTC")
