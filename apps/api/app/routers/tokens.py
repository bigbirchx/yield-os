"""
Token universe endpoints.

Provides paginated, searchable access to the merged token universe (static
ASSET_REGISTRY + CoinGecko top 500).  All data is served from the DB
token_universe table, which is refreshed every 24 hours by the worker.

Endpoints
---------
GET /api/tokens                             — paginated list with filters
GET /api/tokens/{canonical_id}              — single token detail + price + opportunity summary
GET /api/tokens/{canonical_id}/opportunities — convenience redirect to /api/opportunities?asset=
"""
from __future__ import annotations

from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.opportunity import MarketOpportunityRow
from app.models.token_universe import TokenUniverseRow

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/tokens", tags=["tokens"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class TokenResponse(BaseModel):
    canonical_id: str
    coingecko_id: str | None
    name: str
    symbol: str
    umbrella: str
    sub_type: str
    market_cap_rank: int | None
    market_cap_usd: float | None
    current_price_usd: float | None
    price_updated_at: str | None
    chains: list[str]
    is_static: bool
    last_refreshed_at: str


class TokenDetailResponse(TokenResponse):
    opportunity_count: int
    opportunity_count_by_protocol: dict[str, int]
    top_supply_apy: float | None
    top_borrow_apy: float | None


class PaginationMeta(BaseModel):
    total: int
    limit: int
    offset: int
    has_more: bool


class TokenListResponse(BaseModel):
    data: list[TokenResponse]
    pagination: PaginationMeta


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_token(row: TokenUniverseRow) -> TokenResponse:
    return TokenResponse(
        canonical_id=row.canonical_id,
        coingecko_id=row.coingecko_id,
        name=row.name,
        symbol=row.symbol,
        umbrella=row.umbrella,
        sub_type=row.sub_type,
        market_cap_rank=row.market_cap_rank,
        market_cap_usd=row.market_cap_usd,
        current_price_usd=row.current_price_usd,
        price_updated_at=row.price_updated_at.isoformat() if row.price_updated_at else None,
        chains=row.chains or [],
        is_static=row.is_static,
        last_refreshed_at=row.last_refreshed_at.isoformat(),
    )


def _apply_token_filters(q, search, umbrella, min_rank, max_rank):
    if search:
        term = f"%{search.strip().lower()}%"
        q = q.where(
            or_(
                func.lower(TokenUniverseRow.canonical_id).like(term),
                func.lower(TokenUniverseRow.symbol).like(term),
                func.lower(TokenUniverseRow.name).like(term),
                func.lower(TokenUniverseRow.coingecko_id).like(term),
            )
        )
    if umbrella:
        q = q.where(TokenUniverseRow.umbrella == umbrella.upper())
    if min_rank is not None:
        q = q.where(TokenUniverseRow.market_cap_rank >= min_rank)
    if max_rank is not None:
        q = q.where(TokenUniverseRow.market_cap_rank <= max_rank)
    return q


# ---------------------------------------------------------------------------
# Fixed-path routes (must come before /{canonical_id})
# ---------------------------------------------------------------------------


@router.get("", response_model=TokenListResponse)
async def list_tokens(
    search: str | None = Query(None, description="Fuzzy search on symbol, name, coingecko_id"),
    umbrella: str | None = Query(None, description="Filter by umbrella group (USD, ETH, BTC, SOL, HYPE, OTHER)"),
    min_rank: int | None = Query(None, ge=1, description="Minimum market cap rank"),
    max_rank: int | None = Query(None, ge=1, description="Maximum market cap rank"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> TokenListResponse:
    """Paginated token list with search and filtering."""
    q = select(TokenUniverseRow)
    q = _apply_token_filters(q, search, umbrella, min_rank, max_rank)

    count_q = select(func.count()).select_from(q.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    # Sort: static first, then by market cap rank (nulls last)
    q = (
        q.order_by(
            TokenUniverseRow.is_static.desc(),
            TokenUniverseRow.market_cap_rank.asc().nulls_last(),
            TokenUniverseRow.canonical_id.asc(),
        )
        .offset(offset)
        .limit(limit)
    )

    rows = (await db.execute(q)).scalars().all()

    return TokenListResponse(
        data=[_row_to_token(r) for r in rows],
        pagination=PaginationMeta(
            total=total,
            limit=limit,
            offset=offset,
            has_more=(offset + limit) < total,
        ),
    )


# ---------------------------------------------------------------------------
# Path-parameter routes (must come after fixed routes)
# ---------------------------------------------------------------------------


@router.get("/{canonical_id}/opportunities")
async def get_token_opportunities(
    canonical_id: str,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Redirect to /api/opportunities?asset={canonical_id}."""
    return RedirectResponse(
        url=f"/api/opportunities?asset={canonical_id}",
        status_code=307,
    )


@router.get("/{canonical_id}", response_model=TokenDetailResponse)
async def get_token(
    canonical_id: str,
    db: AsyncSession = Depends(get_db),
) -> TokenDetailResponse:
    """Full token detail with live price and opportunity summary."""
    row = (await db.execute(
        select(TokenUniverseRow).where(TokenUniverseRow.canonical_id == canonical_id)
    )).scalar_one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail=f"Token '{canonical_id}' not found")

    # Opportunity counts by protocol
    opp_rows = (await db.execute(
        select(
            MarketOpportunityRow.protocol,
            func.count().label("n"),
        )
        .where(MarketOpportunityRow.asset_id == canonical_id)
        .group_by(MarketOpportunityRow.protocol)
    )).all()

    by_protocol: dict[str, int] = {r.protocol: r.n for r in opp_rows}
    total_opps = sum(by_protocol.values())

    # Best APYs
    top_supply = (await db.execute(
        select(func.max(MarketOpportunityRow.total_apy_pct))
        .where(
            MarketOpportunityRow.asset_id == canonical_id,
            MarketOpportunityRow.side == "SUPPLY",
        )
    )).scalar()

    top_borrow = (await db.execute(
        select(func.min(MarketOpportunityRow.total_apy_pct))
        .where(
            MarketOpportunityRow.asset_id == canonical_id,
            MarketOpportunityRow.side == "BORROW",
        )
    )).scalar()

    # Try to get fresher price from Redis if available
    price = row.current_price_usd
    try:
        from app.services.token_universe import get_price_service
        fresh = await get_price_service().get_price(canonical_id)
        if fresh is not None:
            price = fresh
    except Exception:
        pass

    return TokenDetailResponse(
        canonical_id=row.canonical_id,
        coingecko_id=row.coingecko_id,
        name=row.name,
        symbol=row.symbol,
        umbrella=row.umbrella,
        sub_type=row.sub_type,
        market_cap_rank=row.market_cap_rank,
        market_cap_usd=row.market_cap_usd,
        current_price_usd=price,
        price_updated_at=row.price_updated_at.isoformat() if row.price_updated_at else None,
        chains=row.chains or [],
        is_static=row.is_static,
        last_refreshed_at=row.last_refreshed_at.isoformat(),
        opportunity_count=total_opps,
        opportunity_count_by_protocol=by_protocol,
        top_supply_apy=top_supply,
        top_borrow_apy=top_borrow,
    )
