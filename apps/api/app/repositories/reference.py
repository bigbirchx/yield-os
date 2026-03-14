"""
Reference repository — read-side queries for CoinGecko reference tables.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.reference import (
    ApiUsageSnapshot,
    AssetReferenceMap,
    MarketReferenceHistory,
    MarketReferenceSnapshot,
)


async def get_latest_snapshots(
    db: AsyncSession, symbols: list[str] | None = None
) -> list[MarketReferenceSnapshot]:
    """
    Return the most recent market snapshot per coingecko_id.
    Optionally filtered to the given symbols.
    """
    sub = (
        select(
            MarketReferenceSnapshot.coingecko_id,
            func.max(MarketReferenceSnapshot.snapshot_at).label("max_at"),
        )
        .group_by(MarketReferenceSnapshot.coingecko_id)
        .subquery()
    )
    stmt = select(MarketReferenceSnapshot).join(
        sub,
        (MarketReferenceSnapshot.coingecko_id == sub.c.coingecko_id)
        & (MarketReferenceSnapshot.snapshot_at == sub.c.max_at),
    )
    if symbols:
        syms_upper = [s.upper() for s in symbols]
        stmt = stmt.where(MarketReferenceSnapshot.symbol.in_(syms_upper))
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_snapshot_by_symbol(
    db: AsyncSession, symbol: str
) -> MarketReferenceSnapshot | None:
    sub = (
        select(func.max(MarketReferenceSnapshot.snapshot_at))
        .where(MarketReferenceSnapshot.symbol == symbol.upper())
        .scalar_subquery()
    )
    stmt = select(MarketReferenceSnapshot).where(
        MarketReferenceSnapshot.symbol == symbol.upper(),
        MarketReferenceSnapshot.snapshot_at == sub,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_asset_map(db: AsyncSession, symbol: str) -> AssetReferenceMap | None:
    stmt = select(AssetReferenceMap).where(
        AssetReferenceMap.symbol == symbol.upper()
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_history(
    db: AsyncSession, coingecko_id: str, days: int = 90
) -> list[MarketReferenceHistory]:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    stmt = (
        select(MarketReferenceHistory)
        .where(
            MarketReferenceHistory.coingecko_id == coingecko_id,
            MarketReferenceHistory.snapshot_at >= cutoff,
        )
        .order_by(MarketReferenceHistory.snapshot_at)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_latest_api_usage(db: AsyncSession) -> ApiUsageSnapshot | None:
    stmt = (
        select(ApiUsageSnapshot)
        .order_by(ApiUsageSnapshot.snapshot_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()
