"""
Unified opportunity query endpoints.

Provides the main interface for the frontend to discover, filter, sort, and
drill into yield opportunities across all venues and chains.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.opportunity import (
    MarketOpportunityRow,
    MarketOpportunitySnapshotRow,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/opportunities", tags=["opportunities"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class PaginationMeta(BaseModel):
    total: int
    limit: int
    offset: int
    has_more: bool


class OpportunityListResponse(BaseModel):
    data: list[dict]
    pagination: PaginationMeta


class OpportunitySummary(BaseModel):
    total_opportunities: int
    by_venue: dict[str, int]
    by_chain: dict[str, int]
    by_type: dict[str, int]
    by_umbrella: dict[str, int]
    by_side: dict[str, int]
    top_supply_apy: list[dict] | None = None
    top_borrow_apy: list[dict] | None = None


class RefreshResult(BaseModel):
    triggered_at: datetime
    total_opportunities: int
    by_venue: dict[str, int]
    errors: list[str]
    duration_seconds: float


# ---------------------------------------------------------------------------
# Helpers (defined first so routes can reference them)
# ---------------------------------------------------------------------------


def _resolve_sort_column(sort_by: str):
    """Map a sort_by string to a SQLAlchemy column expression."""
    MO = MarketOpportunityRow
    mapping = {
        "total_apy_pct": MO.total_apy_pct.desc(),
        "apy": MO.total_apy_pct.desc(),
        "tvl_usd": MO.tvl_usd.desc().nulls_last(),
        "tvl": MO.tvl_usd.desc().nulls_last(),
        "last_updated_at": MO.last_updated_at.desc(),
        "asset_id": MO.asset_id.asc(),
        "venue": MO.venue.asc(),
        "chain": MO.chain.asc(),
    }
    return mapping.get(sort_by, MO.total_apy_pct.desc())


def _row_to_dict(row: MarketOpportunityRow) -> dict:
    """Convert a MarketOpportunityRow to a JSON-friendly dict."""
    return {
        "opportunity_id": row.opportunity_id,
        "venue": row.venue,
        "chain": row.chain,
        "protocol": row.protocol,
        "protocol_slug": row.protocol_slug,
        "market_id": row.market_id,
        "market_name": row.market_name,
        "side": row.side,
        "asset_id": row.asset_id,
        "asset_symbol": row.asset_symbol,
        "umbrella_group": row.umbrella_group,
        "asset_sub_type": row.asset_sub_type,
        "opportunity_type": row.opportunity_type,
        "effective_duration": row.effective_duration,
        "maturity_date": row.maturity_date.isoformat() if row.maturity_date else None,
        "days_to_maturity": row.days_to_maturity,
        "total_apy_pct": row.total_apy_pct,
        "base_apy_pct": row.base_apy_pct,
        "reward_breakdown": row.reward_breakdown,
        "total_supplied": row.total_supplied,
        "total_supplied_usd": row.total_supplied_usd,
        "total_borrowed": row.total_borrowed,
        "total_borrowed_usd": row.total_borrowed_usd,
        "capacity_cap": row.capacity_cap,
        "capacity_remaining": row.capacity_remaining,
        "is_capacity_capped": row.is_capacity_capped,
        "tvl_usd": row.tvl_usd,
        "liquidity": row.liquidity,
        "rate_model": row.rate_model,
        "is_collateral_eligible": row.is_collateral_eligible,
        "as_collateral_max_ltv_pct": row.as_collateral_max_ltv_pct,
        "as_collateral_liquidation_ltv_pct": row.as_collateral_liquidation_ltv_pct,
        "collateral_options": row.collateral_options,
        "receipt_token": row.receipt_token,
        "is_amm_lp": row.is_amm_lp,
        "is_pendle": row.is_pendle,
        "pendle_type": row.pendle_type,
        "tags": row.tags,
        "data_source": row.data_source,
        "last_updated_at": row.last_updated_at.isoformat() if row.last_updated_at else None,
        "data_freshness_seconds": row.data_freshness_seconds,
        "source_url": row.source_url,
    }


# ---------------------------------------------------------------------------
# Fixed-path routes (MUST be registered before /{opportunity_id:path})
# ---------------------------------------------------------------------------


@router.get("", response_model=OpportunityListResponse)
async def list_opportunities(
    umbrella: str | None = Query(None, description="Filter by umbrella group (e.g. ETH, USD, BTC)"),
    side: str | None = Query(None, description="SUPPLY or BORROW"),
    type: str | None = Query(None, alias="type", description="Opportunity type (LENDING, VAULT, etc.)"),
    chain: str | None = Query(None, description="Chain (ETHEREUM, ARBITRUM, etc.)"),
    venue: str | None = Query(None, description="Venue (AAVE_V3, MORPHO, etc.)"),
    asset: str | None = Query(None, description="Canonical asset ID (WETH, USDC, etc.)"),
    min_apy: float | None = Query(None, ge=0, description="Minimum total APY percent"),
    min_tvl: float | None = Query(None, ge=0, description="Minimum TVL in USD"),
    exclude_amm_lp: bool = Query(False, description="Exclude AMM LP positions"),
    exclude_pendle: bool = Query(False, description="Exclude Pendle PT/YT"),
    sort_by: str = Query("total_apy_pct", description="Sort field"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> OpportunityListResponse:
    """Query opportunities with filtering, sorting, and pagination."""
    q = select(MarketOpportunityRow)

    if umbrella:
        q = q.where(MarketOpportunityRow.umbrella_group == umbrella.upper())
    if side:
        q = q.where(MarketOpportunityRow.side == side.upper())
    if type:
        q = q.where(MarketOpportunityRow.opportunity_type == type.upper())
    if chain:
        q = q.where(MarketOpportunityRow.chain == chain.upper())
    if venue:
        q = q.where(MarketOpportunityRow.venue == venue.upper())
    if asset:
        q = q.where(MarketOpportunityRow.asset_id == asset)
    if min_apy is not None:
        q = q.where(MarketOpportunityRow.total_apy_pct >= min_apy)
    if min_tvl is not None:
        q = q.where(MarketOpportunityRow.tvl_usd >= min_tvl)
    if exclude_amm_lp:
        q = q.where(MarketOpportunityRow.is_amm_lp == False)  # noqa: E712
    if exclude_pendle:
        q = q.where(MarketOpportunityRow.is_pendle == False)  # noqa: E712

    count_q = select(func.count()).select_from(q.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    sort_col = _resolve_sort_column(sort_by)
    q = q.order_by(sort_col).offset(offset).limit(limit)

    rows = (await db.execute(q)).scalars().all()

    return OpportunityListResponse(
        data=[_row_to_dict(r) for r in rows],
        pagination=PaginationMeta(
            total=total,
            limit=limit,
            offset=offset,
            has_more=(offset + limit) < total,
        ),
    )


@router.get("/summary", response_model=OpportunitySummary)
async def opportunity_summary(
    db: AsyncSession = Depends(get_db),
) -> OpportunitySummary:
    """Aggregated stats across all opportunities."""
    MO = MarketOpportunityRow

    total = (await db.execute(select(func.count()).select_from(MO))).scalar() or 0

    by_venue = dict(
        (await db.execute(select(MO.venue, func.count().label("n")).group_by(MO.venue))).all()
    )
    by_chain = dict(
        (await db.execute(select(MO.chain, func.count().label("n")).group_by(MO.chain))).all()
    )
    by_type = dict(
        (await db.execute(select(MO.opportunity_type, func.count().label("n")).group_by(MO.opportunity_type))).all()
    )
    by_umbrella = dict(
        (await db.execute(select(MO.umbrella_group, func.count().label("n")).group_by(MO.umbrella_group))).all()
    )
    by_side = dict(
        (await db.execute(select(MO.side, func.count().label("n")).group_by(MO.side))).all()
    )

    top_supply = (await db.execute(
        select(MO.opportunity_id, MO.asset_symbol, MO.venue, MO.chain, MO.total_apy_pct)
        .where(MO.side == "SUPPLY")
        .order_by(MO.total_apy_pct.desc())
        .limit(5)
    )).all()

    top_borrow = (await db.execute(
        select(MO.opportunity_id, MO.asset_symbol, MO.venue, MO.chain, MO.total_apy_pct)
        .where(MO.side == "BORROW")
        .order_by(MO.total_apy_pct.asc())
        .limit(5)
    )).all()

    return OpportunitySummary(
        total_opportunities=total,
        by_venue=by_venue,
        by_chain=by_chain,
        by_type=by_type,
        by_umbrella=by_umbrella,
        by_side=by_side,
        top_supply_apy=[
            {"id": r.opportunity_id, "asset": r.asset_symbol, "venue": r.venue, "chain": r.chain, "apy": r.total_apy_pct}
            for r in top_supply
        ],
        top_borrow_apy=[
            {"id": r.opportunity_id, "asset": r.asset_symbol, "venue": r.venue, "chain": r.chain, "apy": r.total_apy_pct}
            for r in top_borrow
        ],
    )


async def _publish_trigger(job: str = "full_ingestion") -> bool:
    """Try to publish a trigger to the worker via Redis pub/sub."""
    try:
        import json

        import redis.asyncio as aioredis
        from app.core.config import settings as app_settings

        r = aioredis.from_url(
            app_settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2.0,
        )
        await r.publish(
            "yos:worker:trigger",
            json.dumps({"job": job}),
        )
        await r.aclose()
        return True
    except Exception:
        return False


@router.post("/refresh", response_model=RefreshResult)
async def trigger_refresh(
    db: AsyncSession = Depends(get_db),
) -> RefreshResult:
    """Trigger full opportunity ingestion.

    Publishes to worker via Redis; falls back to inline if unavailable.
    """
    enqueued = await _publish_trigger("full_ingestion")
    if enqueued:
        return RefreshResult(
            triggered_at=datetime.now(UTC),
            total_opportunities=0,
            by_venue={},
            errors=[],
            duration_seconds=0,
        )

    from app.services.opportunity_ingestion import OpportunityIngestionService

    svc = OpportunityIngestionService()
    result = await svc.run_full_ingestion(db)
    return RefreshResult(
        triggered_at=datetime.now(UTC),
        total_opportunities=result["total_opportunities"],
        by_venue=result["by_venue"],
        errors=result["errors"],
        duration_seconds=result["duration_seconds"],
    )


@router.post("/refresh/{venue}")
async def trigger_adapter_refresh(
    venue: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Trigger a single adapter refresh.

    Publishes to worker via Redis; falls back to inline if unavailable.
    """
    enqueued = await _publish_trigger(f"venue:{venue.upper()}")
    if enqueued:
        return {"venue": venue.upper(), "status": "enqueued_to_worker"}

    from asset_registry import Venue as VenueEnum
    from app.services.opportunity_ingestion import OpportunityIngestionService

    try:
        v = VenueEnum(venue.upper())
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Unknown venue: {venue}")

    svc = OpportunityIngestionService()
    return await svc.run_adapter(db, v)


# ---------------------------------------------------------------------------
# Path-parameter routes (catch-all — MUST be last)
# ---------------------------------------------------------------------------


@router.get("/{opportunity_id:path}/history")
async def get_opportunity_history(
    opportunity_id: str,
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Rate history from snapshot table."""
    exists = (await db.execute(
        select(func.count()).where(
            MarketOpportunityRow.opportunity_id == opportunity_id,
        )
    )).scalar()
    if not exists:
        raise HTTPException(status_code=404, detail=f"Opportunity '{opportunity_id}' not found")

    cutoff = datetime.now(UTC) - timedelta(days=days)
    rows = (await db.execute(
        select(MarketOpportunitySnapshotRow)
        .where(
            MarketOpportunitySnapshotRow.opportunity_id == opportunity_id,
            MarketOpportunitySnapshotRow.snapshot_at >= cutoff,
        )
        .order_by(MarketOpportunitySnapshotRow.snapshot_at.desc())
        .limit(5000)
    )).scalars().all()

    return [
        {
            "snapshot_at": r.snapshot_at.isoformat(),
            "total_apy_pct": r.total_apy_pct,
            "base_apy_pct": r.base_apy_pct,
            "total_supplied": r.total_supplied,
            "total_supplied_usd": r.total_supplied_usd,
            "total_borrowed": r.total_borrowed,
            "total_borrowed_usd": r.total_borrowed_usd,
            "utilization_rate_pct": r.utilization_rate_pct,
            "tvl_usd": r.tvl_usd,
        }
        for r in rows
    ]


@router.get("/{opportunity_id:path}", response_model=dict)
async def get_opportunity(
    opportunity_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get a single opportunity by its deterministic ID."""
    row = (await db.execute(
        select(MarketOpportunityRow).where(
            MarketOpportunityRow.opportunity_id == opportunity_id,
        )
    )).scalar_one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail=f"Opportunity '{opportunity_id}' not found")

    return _row_to_dict(row)
