"""
Derivatives repository — read-side queries for derivatives_snapshots.
"""

from __future__ import annotations

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.snapshot import DerivativesSnapshot


async def get_latest_per_venue(
    db: AsyncSession, symbol: str
) -> list[DerivativesSnapshot]:
    """Return the most recent row for each venue for the given symbol."""
    # Subquery: max snapshot_at per (symbol, venue)
    sub = (
        select(
            DerivativesSnapshot.venue,
            func.max(DerivativesSnapshot.snapshot_at).label("max_at"),
        )
        .where(DerivativesSnapshot.symbol == symbol.upper())
        .group_by(DerivativesSnapshot.venue)
        .subquery()
    )
    stmt = select(DerivativesSnapshot).join(
        sub,
        (DerivativesSnapshot.venue == sub.c.venue)
        & (DerivativesSnapshot.snapshot_at == sub.c.max_at),
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_latest_per_symbol(
    db: AsyncSession, symbols: list[str]
) -> list[DerivativesSnapshot]:
    """
    Return the single most recent row per (symbol, venue) for the
    given list of symbols. Used for the overview endpoint.
    """
    symbols_upper = [s.upper() for s in symbols]

    sub = (
        select(
            DerivativesSnapshot.symbol,
            DerivativesSnapshot.venue,
            func.max(DerivativesSnapshot.snapshot_at).label("max_at"),
        )
        .where(DerivativesSnapshot.symbol.in_(symbols_upper))
        .group_by(DerivativesSnapshot.symbol, DerivativesSnapshot.venue)
        .subquery()
    )
    stmt = select(DerivativesSnapshot).join(
        sub,
        (DerivativesSnapshot.symbol == sub.c.symbol)
        & (DerivativesSnapshot.venue == sub.c.venue)
        & (DerivativesSnapshot.snapshot_at == sub.c.max_at),
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())
