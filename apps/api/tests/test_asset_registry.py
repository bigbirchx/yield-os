"""
Integration tests for the /api/assets/* endpoints.

No database access is required — all responses are served from the in-process
asset-registry package, so no mocking of get_db is needed.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_registry_returns_all_assets():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/assets/registry")
    assert response.status_code == 200
    body = response.json()
    # Registry must contain at least the core assets
    assert "USDC" in body
    assert "ETH" in body
    assert "BTC" in body
    assert "wstETH" in body


@pytest.mark.asyncio
async def test_registry_single_asset():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/assets/registry/USDC")
    assert response.status_code == 200
    body = response.json()
    assert body["canonical_id"] == "USDC"
    assert body["umbrella"] == "USD"


@pytest.mark.asyncio
async def test_registry_single_asset_case_insensitive():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/assets/registry/usdc")
    assert response.status_code == 200
    assert response.json()["canonical_id"] == "USDC"


@pytest.mark.asyncio
async def test_registry_unknown_asset_404():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/assets/registry/NOTANASSET")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_umbrella_eth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/assets/umbrella/ETH")
    assert response.status_code == 200
    body = response.json()
    canonical_ids = [a["canonical_id"] for a in body]
    assert "ETH" in canonical_ids
    assert "wstETH" in canonical_ids
    assert "cbETH" in canonical_ids
    # USD assets must not leak in
    assert "USDC" not in canonical_ids


@pytest.mark.asyncio
async def test_umbrella_invalid_422():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/assets/umbrella/FAKECOIN")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_normalize_binance_btc():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/assets/normalize", params={"venue": "BINANCE", "symbol": "BTC"})
    assert response.status_code == 200
    body = response.json()
    assert body["canonical_id"] == "BTC"
    assert body["resolved"] is True


@pytest.mark.asyncio
async def test_normalize_defillama_chain_prefix():
    """DeFiLlama emits symbols like 'ethereum:USDC' — the normalizer must strip the prefix."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/assets/normalize",
            params={"venue": "DEFILLAMA", "symbol": "ethereum:USDC"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["canonical_id"] == "USDC"


@pytest.mark.asyncio
async def test_conversions_eth_to_wsteth():
    # "wstETH" is the canonical ID; the API accepts any casing via case-insensitive lookup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/assets/conversions",
            params={"from": "ETH", "to": "wstETH", "amount_usd": "1000000"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["paths_found"] >= 1
    first_path = body["paths"][0]
    assert first_path["hops"] >= 1
    assert "cost" in first_path


@pytest.mark.asyncio
async def test_conversions_cross_umbrella_returns_empty():
    """No conversion path should exist between different umbrellas (e.g. ETH -> BTC)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/assets/conversions",
            params={"from": "ETH", "to": "BTC"},
        )
    assert response.status_code == 200
    assert response.json()["paths_found"] == 0


@pytest.mark.asyncio
async def test_fungible_group_usdc():
    # USDC, USDT, DAI, USDS are all FULLY_FUNGIBLE USD assets — they share a group
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/assets/fungible-group/USDC")
    assert response.status_code == 200
    body = response.json()
    assert body["canonical_id"] == "USDC"
    assert "USDC" in body["fungible_group"]
    assert "USDT" in body["fungible_group"]
    assert "DAI" in body["fungible_group"]


@pytest.mark.asyncio
async def test_fungible_group_unknown_404():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/assets/fungible-group/NOTREAL_ASSET")
    assert response.status_code == 404
