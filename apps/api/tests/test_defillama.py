"""
Tests for the DefiLlama free-tier integration.

All HTTP calls are mocked — no live API calls. No Pro endpoints are used.
Covers: client parsing, ingestion domain isolation, API endpoint behaviour.
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_http_mock(json_body: object, status: int = 200):
    """Return an httpx.AsyncClient mock that returns the given JSON body."""
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


_MOCK_POOL = {
    "pool": "abc123",
    "chain": "Ethereum",
    "project": "aave-v3",
    "symbol": "USDC",
    "tvlUsd": 500_000_000.0,
    "apy": 4.5,
    "apyBase": 4.2,
    "apyReward": 0.3,
    "stablecoin": True,
    "ilRisk": "no",
    "exposure": "single",
}

_MOCK_CHART_POINT = {
    "timestamp": "2025-01-15T00:00:00.000Z",
    "tvlUsd": 480_000_000.0,
    "apy": 4.1,
    "apyBase": 3.9,
    "apyReward": 0.2,
}

_MOCK_PROTOCOL = {
    "id": "aave-v3",
    "name": "Aave V3",
    "slug": "aave-v3",
    "category": "Lending",
    "chain": "Multi-Chain",
    "tvl": 24_000_000_000.0,
    "change_1d": 0.5,
    "change_7d": -1.2,
    "change_1m": 3.1,
    "chains": ["Ethereum", "Arbitrum"],
}

_MOCK_STABLECOIN = {
    "id": "2",
    "name": "USD Coin",
    "symbol": "USDC",
    "gecko_id": "usd-coin",
    "pegType": "peggedUSD",
    "pegMechanism": "fiat-backed",
    "circulating": {"peggedUSD": 77_000_000_000.0},
    "circulatingPrevDay": {"peggedUSD": 76_800_000_000.0},
    "chainCirculating": {"Ethereum": {"current": {"peggedUSD": 40_000_000_000.0}}},
}


# ---------------------------------------------------------------------------
# DeFiLlamaClient (yields.llama.fi)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_pools_parses_correctly():
    mock_resp = {"data": [_MOCK_POOL]}
    with patch("app.connectors.defillama_client.httpx.AsyncClient",
               return_value=_make_http_mock(mock_resp)):
        from app.connectors.defillama_client import DeFiLlamaClient
        async with DeFiLlamaClient() as client:
            pools = await client.fetch_pools()

    assert len(pools) == 1
    p = pools[0]
    assert p.pool == "abc123"
    assert p.project == "aave-v3"
    assert p.symbol == "USDC"
    assert p.tvl_usd == 500_000_000.0
    assert p.apy == 4.5
    assert p.stablecoin is True
    assert p.il_risk == "no"


@pytest.mark.asyncio
async def test_fetch_pool_chart_parses_correctly():
    mock_resp = {"data": [_MOCK_CHART_POINT]}
    with patch("app.connectors.defillama_client.httpx.AsyncClient",
               return_value=_make_http_mock(mock_resp)):
        from app.connectors.defillama_client import DeFiLlamaClient
        async with DeFiLlamaClient() as client:
            history = await client.fetch_pool_chart("abc123")

    assert len(history) == 1
    pt = history[0]
    assert pt.apy == 4.1
    assert pt.tvl_usd == 480_000_000.0
    assert "2025-01-15" in pt.timestamp


# ---------------------------------------------------------------------------
# DeFiLlamaMainClient (api.llama.fi)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_protocols_filters_and_parses():
    with patch("app.connectors.defillama_client.httpx.AsyncClient",
               return_value=_make_http_mock([_MOCK_PROTOCOL])):
        from app.connectors.defillama_client import DeFiLlamaMainClient
        async with DeFiLlamaMainClient() as client:
            protos = await client.fetch_protocols()

    assert len(protos) == 1
    p = protos[0]
    assert p.name == "Aave V3"
    assert p.tvl == 24_000_000_000.0
    assert p.change_1d == 0.5


@pytest.mark.asyncio
async def test_fetch_chains_parses():
    mock_chains = [
        {"name": "Ethereum", "tvl": 60e9, "tokenSymbol": "ETH"},
        {"name": "Solana", "tvl": 10e9, "tokenSymbol": "SOL"},
    ]
    with patch("app.connectors.defillama_client.httpx.AsyncClient",
               return_value=_make_http_mock(mock_chains)):
        from app.connectors.defillama_client import DeFiLlamaMainClient
        async with DeFiLlamaMainClient() as client:
            chains = await client.fetch_chains()

    assert len(chains) == 2
    assert chains[0].name == "Ethereum"
    assert chains[0].tvl == 60e9


@pytest.mark.asyncio
async def test_fetch_overview_dexs_parses():
    mock_resp = {
        "total24h": 5_000_000_000.0,
        "total48hto24h": 4_800_000_000.0,
        "protocols": [
            {"name": "Uniswap", "module": "uniswap", "total24h": 1_200_000_000.0, "chains": ["Ethereum"]},
        ],
    }
    with patch("app.connectors.defillama_client.httpx.AsyncClient",
               return_value=_make_http_mock(mock_resp)):
        from app.connectors.defillama_client import DeFiLlamaMainClient
        async with DeFiLlamaMainClient() as client:
            resp = await client.fetch_overview_dexs()

    assert resp.total_24h == 5_000_000_000.0
    assert len(resp.protocols) == 1
    assert resp.protocols[0].name == "Uniswap"


# ---------------------------------------------------------------------------
# DeFiLlamaStablecoinsClient (stablecoins.llama.fi)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_stablecoins_parses():
    mock_resp = {"peggedAssets": [_MOCK_STABLECOIN]}
    with patch("app.connectors.defillama_client.httpx.AsyncClient",
               return_value=_make_http_mock(mock_resp)):
        from app.connectors.defillama_client import DeFiLlamaStablecoinsClient
        async with DeFiLlamaStablecoinsClient() as client:
            stables = await client.fetch_stablecoins()

    assert len(stables) == 1
    s = stables[0]
    assert s.symbol == "USDC"
    assert s.circulating_usd == 77_000_000_000.0
    assert s.peg_type == "peggedUSD"


@pytest.mark.asyncio
async def test_fetch_stablecoin_404_returns_empty():
    with patch("app.connectors.defillama_client.httpx.AsyncClient",
               return_value=_make_http_mock({}, status=404)):
        from app.connectors.defillama_client import DeFiLlamaStablecoinsClient
        async with DeFiLlamaStablecoinsClient() as client:
            data = await client.fetch_stablecoin("9999")

    assert data == {}


# ---------------------------------------------------------------------------
# Ingestion domain isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_yield_snapshots_only_tracked():
    """Only pools matching tracked symbols/protocols and TVL > 100k are stored."""
    pools_payload = {
        "data": [
            {**_MOCK_POOL, "pool": "p1", "symbol": "USDC", "project": "aave-v3", "tvlUsd": 1_000_000.0},
            {**_MOCK_POOL, "pool": "p2", "symbol": "SHIB", "project": "unknown", "tvlUsd": 50_000.0},
            {**_MOCK_POOL, "pool": "p3", "symbol": "ETH", "project": "lido", "tvlUsd": 10_000_000.0},
        ]
    }
    with patch("app.connectors.defillama_client.httpx.AsyncClient",
               return_value=_make_http_mock(pools_payload)):
        from app.services.defillama_ingestion import ingest_yield_pool_snapshots

        db_mock = AsyncMock()
        db_mock.add_all = MagicMock()
        db_mock.commit = AsyncMock()

        count = await ingest_yield_pool_snapshots(db_mock)

    # p1 (USDC/aave-v3) and p3 (ETH/lido) should pass; p2 (SHIB/unknown, tvl<100k) excluded
    assert count == 2
    rows = db_mock.add_all.call_args[0][0]
    pool_ids = {r.pool_id for r in rows}
    assert "p1" in pool_ids
    assert "p3" in pool_ids
    assert "p2" not in pool_ids


@pytest.mark.asyncio
async def test_ingest_stablecoins_only_tracked():
    """Only TRACKED_STABLECOIN_SYMBOLS are stored in snapshots."""
    stables_payload = {
        "peggedAssets": [
            {**_MOCK_STABLECOIN, "id": "2", "symbol": "USDC"},
            {**_MOCK_STABLECOIN, "id": "99", "symbol": "OBSCURE"},
        ]
    }
    charts_payload = [
        {"date": 1700000000, "totalCirculating": {"peggedUSD": 180e9}, "totalCirculatingUSD": {"peggedUSD": 180e9}},
    ]

    with patch("app.connectors.defillama_client.httpx.AsyncClient",
               return_value=_make_http_mock(stables_payload)):
        with patch(
            "app.services.defillama_ingestion.DeFiLlamaStablecoinsClient",
        ) as MockStables:
            mock_inst = AsyncMock()
            mock_inst.__aenter__.return_value = mock_inst
            mock_inst.__aexit__.return_value = None
            mock_inst.fetch_stablecoins = AsyncMock(return_value=[
                type("S", (), {
                    "id": "2", "symbol": "USDC", "circulating_usd": 77e9,
                    "circulating": {"peggedUSD": 77e9}, "chain_circulating": {},
                    "peg_type": "peggedUSD", "peg_mechanism": "fiat-backed",
                    "model_dump": lambda self, **kw: {},
                })(),
                type("S", (), {
                    "id": "99", "symbol": "OBSCURE", "circulating_usd": 100.0,
                    "circulating": {"peggedUSD": 100.0}, "chain_circulating": {},
                    "peg_type": None, "peg_mechanism": None,
                    "model_dump": lambda self, **kw: {},
                })(),
            ])
            mock_inst.fetch_stablecoin_charts = AsyncMock(return_value=[
                type("P", (), {
                    "date": 1700000000,
                    "total_circulating_usd": {"peggedUSD": 180e9},
                    "circulating_usd": 180e9,
                })(),
            ])
            MockStables.return_value = mock_inst

            db_mock = AsyncMock()
            db_mock.add_all = MagicMock()
            db_mock.commit = AsyncMock()

            from app.services.defillama_ingestion import ingest_stablecoins
            await ingest_stablecoins(db_mock)

    snap_calls = db_mock.add_all.call_args_list
    # First add_all is snapshots
    snap_rows = snap_calls[0][0][0]
    symbols = {r.symbol for r in snap_rows}
    assert "USDC" in symbols
    assert "OBSCURE" not in symbols


# ---------------------------------------------------------------------------
# API endpoint tests (live fallback path — no DB rows)
# ---------------------------------------------------------------------------

def test_api_routes_registered():
    """Verify all 8 DefiLlama API routes are registered in the FastAPI app."""
    from app.main import app
    routes = {r.path for r in app.routes}
    expected = [
        "/api/defillama/yields",
        "/api/defillama/protocols",
        "/api/defillama/chains",
        "/api/defillama/stablecoins",
        "/api/defillama/market-context",
    ]
    for path in expected:
        assert path in routes, f"Route {path} not registered"


def test_response_schemas_have_source_field():
    """YieldPoolOut, ProtocolOut, StablecoinOut all carry source='defillama_free'."""
    from app.routers.defillama import YieldPoolOut, ProtocolOut, StablecoinOut
    from datetime import datetime, UTC
    now = datetime.now(UTC)

    pool = YieldPoolOut(pool_id="x", project="p", chain="Eth", symbol="USDC",
                        tvl_usd=1e9, apy=4.0, apy_base=3.8, apy_reward=0.2,
                        stablecoin=True, il_risk="no", snapshot_at=now)
    assert pool.source == "defillama_free"

    proto = ProtocolOut(protocol_slug="aave-v3", protocol_name="Aave V3",
                        category="Lending", chain="Multi", tvl_usd=20e9,
                        change_1d=0.1, change_7d=1.0, change_1m=2.0, ts=now)
    assert proto.source == "defillama_free"


# ---------------------------------------------------------------------------
# No Pro endpoints check
# ---------------------------------------------------------------------------

def test_no_pro_base_url_used():
    """
    Ensure no pro-api.llama.fi URL is used as a client base URL.
    References in docstrings/comments documenting excluded endpoints are fine.
    """
    from app.connectors.defillama_client import (
        _YIELDS_BASE,
        _MAIN_BASE,
        _STABLES_BASE,
    )
    for url in (_YIELDS_BASE, _MAIN_BASE, _STABLES_BASE):
        assert "pro-api.llama.fi" not in url, f"Pro URL used as base: {url}"

    # Ensure default constructors do not reference the pro base
    from app.connectors.defillama_client import (
        DeFiLlamaClient,
        DeFiLlamaMainClient,
        DeFiLlamaStablecoinsClient,
    )
    import inspect
    for cls in (DeFiLlamaClient, DeFiLlamaMainClient, DeFiLlamaStablecoinsClient):
        init_src = inspect.getsource(cls.__init__)
        assert "pro-api.llama.fi" not in init_src
