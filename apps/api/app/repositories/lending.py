"""
Lending repository — read-side queries for lending_market_snapshots.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.snapshot import LendingMarketSnapshot


async def get_latest_per_market(
    db: AsyncSession, symbols: list[str]
) -> list[LendingMarketSnapshot]:
    """
    Return the most recent snapshot per (symbol, protocol, market) for the
    given symbols. Used for the overview endpoint.
    """
    symbols_upper = [s.upper() for s in symbols]

    sub = (
        select(
            LendingMarketSnapshot.symbol,
            LendingMarketSnapshot.protocol,
            LendingMarketSnapshot.market,
            func.max(LendingMarketSnapshot.snapshot_at).label("max_at"),
        )
        .where(LendingMarketSnapshot.symbol.in_(symbols_upper))
        .group_by(
            LendingMarketSnapshot.symbol,
            LendingMarketSnapshot.protocol,
            LendingMarketSnapshot.market,
        )
        .subquery()
    )
    stmt = select(LendingMarketSnapshot).join(
        sub,
        (LendingMarketSnapshot.symbol == sub.c.symbol)
        & (LendingMarketSnapshot.protocol == sub.c.protocol)
        & (LendingMarketSnapshot.market == sub.c.market)
        & (LendingMarketSnapshot.snapshot_at == sub.c.max_at),
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_history(
    db: AsyncSession,
    symbols: list[str],
    days: int = 30,
    protocol: str | None = None,
) -> list[LendingMarketSnapshot]:
    """
    Return time-series rows for the given symbols over the last `days` days.
    Optionally filter by protocol. Results are ordered by snapshot_at ascending.
    """
    symbols_upper = [s.upper() for s in symbols]
    since = datetime.now(UTC) - timedelta(days=days)

    stmt = (
        select(LendingMarketSnapshot)
        .where(
            LendingMarketSnapshot.symbol.in_(symbols_upper),
            LendingMarketSnapshot.snapshot_at >= since,
        )
        .order_by(LendingMarketSnapshot.snapshot_at.asc())
    )
    if protocol:
        stmt = stmt.where(LendingMarketSnapshot.protocol == protocol)

    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_pool_ids_for_symbol(
    db: AsyncSession, symbols: list[str]
) -> list[tuple[str, str, str, str]]:
    """
    Return distinct (pool_id, symbol, protocol, chain) tuples for the given
    symbols. Used by backfill jobs to discover which pools to fetch history for.
    """
    symbols_upper = [s.upper() for s in symbols]
    stmt = (
        select(
            LendingMarketSnapshot.pool_id,
            LendingMarketSnapshot.symbol,
            LendingMarketSnapshot.protocol,
            LendingMarketSnapshot.chain,
        )
        .where(
            LendingMarketSnapshot.symbol.in_(symbols_upper),
            LendingMarketSnapshot.pool_id.isnot(None),
        )
        .distinct()
    )
    result = await db.execute(stmt)
    return list(result.all())
