"""
Tests for DeFiLlamaClient with mocked HTTP responses.
"""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.connectors.defillama_client import DeFiLlamaClient

BASE_URL = "https://yields.llama.fi"

MOCK_POOLS = [
    {
        "pool": "aa-bb-cc-1",
        "chain": "Ethereum",
        "project": "aave-v3",
        "symbol": "USDC",
        "tvlUsd": 1_500_000_000.0,
        "apy": 5.2,
        "apyBase": 4.8,
        "apyReward": 0.4,
        "apyBaseBorrow": 6.1,
        "apyRewardBorrow": 0.2,
        "totalSupplyUsd": 2_000_000_000.0,
        "totalBorrowUsd": 1_500_000_000.0,
        "ltv": 0.77,
        "poolMeta": None,
        "underlyingTokens": ["0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"],
        "rewardTokens": [],
    },
    {
        "pool": "dd-ee-ff-2",
        "chain": "Ethereum",
        "project": "lido",
        "symbol": "STETH",
        "tvlUsd": 28_000_000_000.0,
        "apy": 3.8,
        "apyBase": 3.8,
        "apyReward": None,
        "apyBaseBorrow": None,
        "apyRewardBorrow": None,
        "totalSupplyUsd": None,
        "totalBorrowUsd": None,
        "ltv": None,
        "poolMeta": None,
        "underlyingTokens": [],
        "rewardTokens": [],
    },
]

MOCK_CHART = {
    "status": "ok",
    "data": [
        {"timestamp": "2026-01-01T00:00:00Z", "tvlUsd": 1_400_000_000.0, "apy": 4.9, "apyBase": 4.5, "apyReward": 0.4, "apyBaseBorrow": 5.8},
        {"timestamp": "2026-01-02T00:00:00Z", "tvlUsd": 1_450_000_000.0, "apy": 5.0, "apyBase": 4.6, "apyReward": 0.4, "apyBaseBorrow": 5.9},
        {"timestamp": "2026-01-03T00:00:00Z", "tvlUsd": 1_500_000_000.0, "apy": 5.2, "apyBase": 4.8, "apyReward": 0.4, "apyBaseBorrow": 6.1},
    ],
}


@pytest.mark.asyncio
@respx.mock
async def test_fetch_pools_success():
    respx.get(f"{BASE_URL}/pools").mock(return_value=Response(200, json={"data": MOCK_POOLS}))

    async with DeFiLlamaClient(base_url=BASE_URL) as client:
        pools = await client.fetch_pools()

    assert len(pools) == 2

    usdc_pool = next(p for p in pools if p.symbol == "USDC")
    assert usdc_pool.project == "aave-v3"
    assert usdc_pool.chain == "Ethereum"
    assert usdc_pool.tvl_usd == pytest.approx(1_500_000_000.0)
    assert usdc_pool.apy_base == pytest.approx(4.8)
    assert usdc_pool.apy_base_borrow == pytest.approx(6.1)
    assert usdc_pool.pool == "aa-bb-cc-1"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_pools_bare_list():
    """Handles bare list response without data envelope."""
    respx.get(f"{BASE_URL}/pools").mock(return_value=Response(200, json=MOCK_POOLS))

    async with DeFiLlamaClient(base_url=BASE_URL) as client:
        pools = await client.fetch_pools()

    assert len(pools) == 2


@pytest.mark.asyncio
@respx.mock
async def test_pool_utilization_and_liquidity():
    respx.get(f"{BASE_URL}/pools").mock(return_value=Response(200, json={"data": MOCK_POOLS}))

    async with DeFiLlamaClient(base_url=BASE_URL) as client:
        pools = await client.fetch_pools()

    usdc = next(p for p in pools if p.symbol == "USDC")
    assert usdc.utilization == pytest.approx(0.75)
    assert usdc.available_liquidity_usd == pytest.approx(500_000_000.0)


@pytest.mark.asyncio
@respx.mock
async def test_pool_null_supply_borrow():
    """Pools without totalSupplyUsd/totalBorrowUsd yield None for utilization."""
    respx.get(f"{BASE_URL}/pools").mock(return_value=Response(200, json={"data": MOCK_POOLS}))

    async with DeFiLlamaClient(base_url=BASE_URL) as client:
        pools = await client.fetch_pools()

    steth = next(p for p in pools if p.symbol == "STETH")
    assert steth.utilization is None
    assert steth.available_liquidity_usd is None


@pytest.mark.asyncio
@respx.mock
async def test_fetch_pool_chart():
    pool_id = "aa-bb-cc-1"
    respx.get(f"{BASE_URL}/chart/{pool_id}").mock(
        return_value=Response(200, json=MOCK_CHART)
    )

    async with DeFiLlamaClient(base_url=BASE_URL) as client:
        history = await client.fetch_pool_chart(pool_id)

    assert len(history) == 3
    assert history[0].timestamp == "2026-01-01T00:00:00Z"
    assert history[0].apy_base == pytest.approx(4.5)
    assert history[2].tvl_usd == pytest.approx(1_500_000_000.0)


@pytest.mark.asyncio
@respx.mock
async def test_http_error_propagates():
    respx.get(f"{BASE_URL}/pools").mock(return_value=Response(429, json={"error": "rate limited"}))

    import httpx

    async with DeFiLlamaClient(base_url=BASE_URL) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await client.fetch_pools()
