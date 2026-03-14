from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.staking import StakingSnapshot

router = APIRouter(prefix="/api/staking", tags=["staking"])


class StakingSnapshotOut(BaseModel):
    symbol: str
    underlying_symbol: str
    protocol: str
    chain: str
    pool_id: str | None
    staking_apy: float | None
    base_apy: float | None
    reward_apy: float | None
    tvl_usd: float | None
    snapshot_at: datetime
    ingested_at: datetime

    model_config = {"from_attributes": True}


@router.get("/{symbol}", response_model=list[StakingSnapshotOut])
async def staking_by_symbol(
    symbol: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Returns the latest staking/LSD snapshot per (symbol, protocol) for the
    requested symbol or underlying symbol.

    Source: DeFiLlama
    """
    sym_upper = symbol.upper()
    # Match on either the staking token (STETH) or the underlying (ETH)
    sub = (
        select(
            StakingSnapshot.symbol,
            StakingSnapshot.protocol,
            func.max(StakingSnapshot.snapshot_at).label("max_at"),
        )
        .where(
            (StakingSnapshot.symbol == sym_upper)
            | (StakingSnapshot.underlying_symbol == sym_upper)
        )
        .group_by(StakingSnapshot.symbol, StakingSnapshot.protocol)
        .subquery()
    )
    stmt = select(StakingSnapshot).join(
        sub,
        (StakingSnapshot.symbol == sub.c.symbol)
        & (StakingSnapshot.protocol == sub.c.protocol)
        & (StakingSnapshot.snapshot_at == sub.c.max_at),
    )
    result = await db.execute(stmt)
    rows = list(result.scalars().all())
    return [StakingSnapshotOut.model_validate(r) for r in rows]
