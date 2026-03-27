"""
DefiLlama free-tier API endpoints.

All data is sourced exclusively from free DefiLlama endpoints —
no Pro-only datasets, no API key required.

Endpoints
---------
GET /api/defillama/yields
    Filtered yield pool snapshots for tracked assets/chains/projects.
    Falls back to live /pools call if DB is empty.

GET /api/defillama/yields/{pool_id}/history
    APY/TVL daily history for one pool from /chart/{pool_id}.
    Always fetches live (not cached) to stay current.

GET /api/defillama/protocols
    Protocol TVL snapshots for tracked protocols.

GET /api/defillama/protocols/{slug}
    Live protocol detail + TVL history from /protocol/{slug}.

GET /api/defillama/chains
    Current + recent TVL context for tracked chains.

GET /api/defillama/stablecoins
    Stablecoin supply/distribution context snapshot.

GET /api/defillama/stablecoins/{asset_id}
    Single stablecoin detail with chain breakdown.

GET /api/defillama/market-context
    DEX volume, open-interest, and fees summaries.
"""

from __future__ import annotations
import asyncio

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.defillama_client import (
    DeFiLlamaClient,
    DeFiLlamaMainClient,
    DeFiLlamaStablecoinsClient,
)
from app.core.config import settings
from app.core.database import get_db
from app.models.defillama import (
    DLChainTvlHistory,
    DLMarketContextSnapshot,
    DLProtocolSnapshot,
    DLStablecoinSnapshot,
    DLYieldPoolHistory,
    DLYieldPoolSnapshot,
)
from app.services.defillama_ingestion import TRACKED_CHAINS

router = APIRouter(prefix="/api/defillama", tags=["defillama"])

_STALENESS_HOURS = 4  # treat DB data as fresh if < 4h old


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class YieldPoolOut(BaseModel):
    pool_id: str
    project: str
    chain: str
    symbol: str
    tvl_usd: float | None
    apy: float | None
    apy_base: float | None
    apy_reward: float | None
    stablecoin: bool | None
    il_risk: str | None
    snapshot_at: datetime | None
    source: str = "defillama_free"

    model_config = {"from_attributes": True}


class YieldHistoryPoint(BaseModel):
    ts: datetime
    apy: float | None
    tvl_usd: float | None
    apy_base: float | None
    apy_reward: float | None


class ProtocolOut(BaseModel):
    protocol_slug: str
    protocol_name: str
    category: str | None
    chain: str | None
    tvl_usd: float | None
    change_1d: float | None
    change_7d: float | None
    change_1m: float | None
    ts: datetime
    source: str = "defillama_free"

    model_config = {"from_attributes": True}


class ChainTvlPoint(BaseModel):
    ts: datetime
    chain: str
    tvl_usd: float


class StablecoinOut(BaseModel):
    stablecoin_id: str
    symbol: str
    circulating_usd: float | None
    peg_type: str | None
    peg_mechanism: str | None
    chains: dict | None
    ts: datetime
    source: str = "defillama_free"

    model_config = {"from_attributes": True}


class MarketContextOut(BaseModel):
    context_type: str
    protocol_or_chain: str
    metric_name: str
    metric_value: float | None
    ts: datetime
    source: str = "defillama_free"

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_fresh(ts: datetime | None, hours: int = _STALENESS_HOURS) -> bool:
    if ts is None:
        return False
    return (datetime.now(UTC) - ts.replace(tzinfo=UTC)) < timedelta(hours=hours)


# ---------------------------------------------------------------------------
# /yields
# ---------------------------------------------------------------------------

@router.get("/yields", response_model=list[YieldPoolOut])
async def get_yields(
    symbol: str | None = Query(None, description="Filter by base symbol (BTC, ETH, USDC…)"),
    chain: str | None = Query(None, description="Filter by chain name"),
    project: str | None = Query(None, description="Filter by protocol/project slug"),
    min_tvl: float = Query(1_000_000, description="Minimum TVL (USD)"),
    min_apy: float = Query(0.0, description="Minimum APY (%)"),
    limit: int = Query(100, le=500),
    db: AsyncSession = Depends(get_db),
) -> list[YieldPoolOut]:
    """
    Filtered yield pool snapshots from the DB (latest snapshot per pool).
    Falls back to a live /pools call if DB is empty.
    Source: DefiLlama /pools (free).
    """
    # Latest snapshot per pool from DB
    sub = (
        select(
            DLYieldPoolSnapshot.pool_id,
            func.max(DLYieldPoolSnapshot.snapshot_at).label("max_at"),
        )
        .group_by(DLYieldPoolSnapshot.pool_id)
        .subquery()
    )
    stmt = (
        select(DLYieldPoolSnapshot)
        .join(
            sub,
            (DLYieldPoolSnapshot.pool_id == sub.c.pool_id)
            & (DLYieldPoolSnapshot.snapshot_at == sub.c.max_at),
        )
    )
    if symbol:
        stmt = stmt.where(DLYieldPoolSnapshot.symbol == symbol.upper())
    if chain:
        stmt = stmt.where(DLYieldPoolSnapshot.chain.ilike(chain))
    if project:
        stmt = stmt.where(DLYieldPoolSnapshot.project.ilike(f"%{project}%"))
    if min_tvl > 0:
        stmt = stmt.where(DLYieldPoolSnapshot.tvl_usd >= min_tvl)
    if min_apy > 0:
        stmt = stmt.where(DLYieldPoolSnapshot.apy >= min_apy)
    stmt = stmt.order_by(DLYieldPoolSnapshot.tvl_usd.desc().nulls_last()).limit(limit)

    result = await db.execute(stmt)
    rows = list(result.scalars().all())

    if rows:
        return [
            YieldPoolOut(
                pool_id=r.pool_id, project=r.project, chain=r.chain,
                symbol=r.symbol, tvl_usd=r.tvl_usd, apy=r.apy,
                apy_base=r.apy_base, apy_reward=r.apy_reward,
                stablecoin=r.stablecoin, il_risk=r.il_risk,
                snapshot_at=r.snapshot_at,
            )
            for r in rows
        ]

    # Live fallback
    async with DeFiLlamaClient(api_key=settings.defillama_api_key) as client:
        pools = await client.fetch_pools()

    now = datetime.now(UTC)
    out = []
    for p in pools:
        if symbol and p.symbol.upper() != symbol.upper():
            continue
        if chain and chain.lower() not in p.chain.lower():
            continue
        if project and project.lower() not in p.project.lower():
            continue
        if p.tvl_usd and p.tvl_usd < min_tvl:
            continue
        if p.apy and p.apy < min_apy:
            continue
        out.append(YieldPoolOut(
            pool_id=p.pool, project=p.project, chain=p.chain,
            symbol=p.symbol.upper(), tvl_usd=p.tvl_usd, apy=p.apy,
            apy_base=p.apy_base, apy_reward=p.apy_reward,
            stablecoin=p.stablecoin, il_risk=p.il_risk, snapshot_at=now,
        ))
    out.sort(key=lambda x: x.tvl_usd or 0, reverse=True)
    return out[:limit]


# ---------------------------------------------------------------------------
# /yields/{pool_id}/history
# ---------------------------------------------------------------------------

@router.get("/yields/{pool_id}/history", response_model=list[YieldHistoryPoint])
async def get_yield_history(
    pool_id: str,
    db: AsyncSession = Depends(get_db),
) -> list[YieldHistoryPoint]:
    """
    Daily APY/TVL history for one pool. Checks DB first, falls back to live
    DefiLlama /chart/{pool_id} call.
    Source: DefiLlama /chart/{pool_id} (free).
    """
    stmt = (
        select(DLYieldPoolHistory)
        .where(DLYieldPoolHistory.pool_id == pool_id)
        .order_by(DLYieldPoolHistory.ts.asc())
    )
    result = await db.execute(stmt)
    rows = list(result.scalars().all())

    if rows:
        return [
            YieldHistoryPoint(ts=r.ts, apy=r.apy, tvl_usd=r.tvl_usd,
                              apy_base=r.apy_base, apy_reward=r.apy_reward)
            for r in rows
        ]

    # Live fallback
    async with DeFiLlamaClient(api_key=settings.defillama_api_key) as client:
        history = await client.fetch_pool_chart(pool_id)
    return [
        YieldHistoryPoint(
            ts=datetime.fromisoformat(p.timestamp.replace("Z", "+00:00")),
            apy=p.apy, tvl_usd=p.tvl_usd,
            apy_base=p.apy_base, apy_reward=p.apy_reward,
        )
        for p in history
    ]


# ---------------------------------------------------------------------------
# /protocols
# ---------------------------------------------------------------------------

@router.get("/protocols", response_model=list[ProtocolOut])
async def get_protocols(
    db: AsyncSession = Depends(get_db),
) -> list[ProtocolOut]:
    """
    Latest protocol TVL snapshots for tracked protocols.
    Source: DefiLlama /protocols (free).
    """
    sub = (
        select(
            DLProtocolSnapshot.protocol_slug,
            func.max(DLProtocolSnapshot.ts).label("max_ts"),
        )
        .group_by(DLProtocolSnapshot.protocol_slug)
        .subquery()
    )
    stmt = (
        select(DLProtocolSnapshot)
        .join(sub, (DLProtocolSnapshot.protocol_slug == sub.c.protocol_slug)
              & (DLProtocolSnapshot.ts == sub.c.max_ts))
        .order_by(DLProtocolSnapshot.tvl_usd.desc().nulls_last())
    )
    result = await db.execute(stmt)
    rows = list(result.scalars().all())

    if rows:
        return [ProtocolOut.model_validate(r) for r in rows]

    # Live fallback
    async with DeFiLlamaMainClient() as client:
        protos = await client.fetch_protocols()
    from app.services.defillama_ingestion import TRACKED_PROTOCOLS
    now = datetime.now(UTC)
    return [
        ProtocolOut(
            protocol_slug=p.slug or p.name.lower().replace(" ", "-"),
            protocol_name=p.name,
            category=p.category,
            chain=p.chain,
            tvl_usd=p.tvl,
            change_1d=p.change_1d,
            change_7d=p.change_7d,
            change_1m=p.change_1m,
            ts=now,
        )
        for p in protos
        if (p.slug or p.name.lower()) in TRACKED_PROTOCOLS
    ]


# ---------------------------------------------------------------------------
# /protocols/{slug}
# ---------------------------------------------------------------------------

@router.get("/protocols/{slug}")
async def get_protocol_detail(slug: str) -> dict[str, Any]:
    """
    Live protocol detail with TVL history breakdown from /protocol/{slug}.
    Source: DefiLlama /protocol/{slug} (free).
    """
    async with DeFiLlamaMainClient() as client:
        data = await client.fetch_protocol(slug)
    if not data:
        raise HTTPException(status_code=404, detail=f"Protocol '{slug}' not found on DefiLlama")
    return {"slug": slug, "source": "defillama_free", "data": data}


# ---------------------------------------------------------------------------
# /chains
# ---------------------------------------------------------------------------

@router.get("/chains")
async def get_chains(
    days: int = Query(90, ge=1, le=365, description="Historical TVL days to return per chain"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Current TVL snapshot + recent history for tracked chains.
    Source: DefiLlama /v2/chains and /v2/historicalChainTvl/{chain} (free).
    """
    cutoff = datetime.now(UTC) - timedelta(days=days)
    stmt = (
        select(DLChainTvlHistory)
        .where(DLChainTvlHistory.ts >= cutoff)
        .where(DLChainTvlHistory.chain.in_(TRACKED_CHAINS))
        .order_by(DLChainTvlHistory.chain, DLChainTvlHistory.ts)
    )
    result = await db.execute(stmt)
    rows = list(result.scalars().all())

    history_by_chain: dict[str, list[dict]] = {}
    for r in rows:
        history_by_chain.setdefault(r.chain, []).append(
            {"ts": r.ts.isoformat(), "tvl_usd": r.tvl_usd}
        )

    # Current TVL (live call — lightweight)
    try:
        async with DeFiLlamaMainClient() as client:
            chains = await client.fetch_chains()
        current = {
            c.name: c.tvl
            for c in chains
            if c.name in TRACKED_CHAINS
        }
    except Exception:
        current = {}

    return {
        "source": "defillama_free",
        "tracked_chains": TRACKED_CHAINS,
        "current_tvl": current,
        "history": history_by_chain,
    }


# ---------------------------------------------------------------------------
# /stablecoins
# ---------------------------------------------------------------------------

@router.get("/stablecoins", response_model=list[StablecoinOut])
async def get_stablecoins(
    db: AsyncSession = Depends(get_db),
) -> list[StablecoinOut]:
    """
    Latest stablecoin supply/distribution snapshot for tracked stablecoins.
    Source: DefiLlama /stablecoins (free).
    """
    sub = (
        select(
            DLStablecoinSnapshot.stablecoin_id,
            func.max(DLStablecoinSnapshot.ts).label("max_ts"),
        )
        .group_by(DLStablecoinSnapshot.stablecoin_id)
        .subquery()
    )
    stmt = (
        select(DLStablecoinSnapshot)
        .join(sub, (DLStablecoinSnapshot.stablecoin_id == sub.c.stablecoin_id)
              & (DLStablecoinSnapshot.ts == sub.c.max_ts))
        .order_by(DLStablecoinSnapshot.circulating_usd.desc().nulls_last())
    )
    result = await db.execute(stmt)
    rows = list(result.scalars().all())

    if rows:
        return [StablecoinOut.model_validate(r) for r in rows]

    # Live fallback
    async with DeFiLlamaStablecoinsClient() as client:
        stables = await client.fetch_stablecoins()
    from app.services.defillama_ingestion import TRACKED_STABLECOIN_SYMBOLS
    now = datetime.now(UTC)
    return [
        StablecoinOut(
            stablecoin_id=str(s.id),
            symbol=s.symbol.upper(),
            circulating_usd=s.circulating_usd,
            peg_type=s.peg_type,
            peg_mechanism=s.peg_mechanism,
            chains=s.chain_circulating,
            ts=now,
        )
        for s in stables
        if s.symbol.upper() in TRACKED_STABLECOIN_SYMBOLS
    ]


# ---------------------------------------------------------------------------
# /stablecoins/{asset_id}
# ---------------------------------------------------------------------------

@router.get("/stablecoins/{asset_id}")
async def get_stablecoin_detail(asset_id: str) -> dict[str, Any]:
    """
    Single stablecoin detail with chain breakdown from /stablecoin/{id}.
    Source: DefiLlama /stablecoin/{id} (free).
    """
    async with DeFiLlamaStablecoinsClient() as client:
        data = await client.fetch_stablecoin(asset_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"Stablecoin id '{asset_id}' not found")
    return {"asset_id": asset_id, "source": "defillama_free", "data": data}


# ---------------------------------------------------------------------------
# /market-context
# ---------------------------------------------------------------------------

@router.get("/market-context")
async def get_market_context(
    context_type: str | None = Query(
        None,
        description="Filter by type: dex_volume | open_interest | fees_revenue",
    ),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    DEX volume, open-interest, and fees/revenue context from latest ingestion.
    Returns aggregate totals + per-protocol breakdown for each context type.
    Source: DefiLlama /overview/dexs, /overview/open-interest, /overview/fees (free).
    """
    sub = (
        select(
            DLMarketContextSnapshot.context_type,
            DLMarketContextSnapshot.protocol_or_chain,
            func.max(DLMarketContextSnapshot.ts).label("max_ts"),
        )
        .group_by(DLMarketContextSnapshot.context_type,
                  DLMarketContextSnapshot.protocol_or_chain)
        .subquery()
    )
    stmt = (
        select(DLMarketContextSnapshot)
        .join(sub,
              (DLMarketContextSnapshot.context_type == sub.c.context_type)
              & (DLMarketContextSnapshot.protocol_or_chain == sub.c.protocol_or_chain)
              & (DLMarketContextSnapshot.ts == sub.c.max_ts))
    )
    if context_type:
        stmt = stmt.where(DLMarketContextSnapshot.context_type == context_type)
    stmt = stmt.order_by(DLMarketContextSnapshot.context_type,
                         DLMarketContextSnapshot.metric_value.desc().nulls_last())

    result = await db.execute(stmt)
    rows = list(result.scalars().all())

    if rows:
        by_type: dict[str, dict] = {}
        for r in rows:
            ct = r.context_type
            if ct not in by_type:
                by_type[ct] = {"aggregate": None, "protocols": []}
            entry = {"protocol": r.protocol_or_chain, "value_24h": r.metric_value, "ts": r.ts.isoformat()}
            if r.protocol_or_chain == "_aggregate":
                by_type[ct]["aggregate"] = r.metric_value
            else:
                by_type[ct]["protocols"].append(entry)
        return {"source": "defillama_free", "as_of": rows[0].ts.isoformat(), "context": by_type}

    # Live fallback

    async with DeFiLlamaMainClient() as client:
        dexs, oi, fees = await asyncio.gather(
            client.fetch_overview_dexs(),
            client.fetch_overview_open_interest(),
            client.fetch_overview_fees(),
            return_exceptions=True,
        )

    def _fmt(resp, ctype: str) -> dict:
        if isinstance(resp, Exception):
            return {"aggregate": None, "protocols": [], "error": str(resp)}
        return {
            "aggregate": resp.total_24h,
            "protocols": [
                {"protocol": (p.slug or p.name), "value_24h": p.total_24h}
                for p in sorted(resp.protocols, key=lambda x: x.total_24h or 0, reverse=True)[:30]
            ],
        }

    return {
        "source": "defillama_free",
        "as_of": datetime.now(UTC).isoformat(),
        "context": {
            "dex_volume": _fmt(dexs, "dex_volume"),
            "open_interest": _fmt(oi, "open_interest"),
            "fees_revenue": _fmt(fees, "fees_revenue"),
        },
    }
