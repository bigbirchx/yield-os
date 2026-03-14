"""
Risk params repository — read-side queries for protocol_risk_params_snapshots.
"""

from __future__ import annotations

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.risk import ProtocolRiskParamsSnapshot


async def get_latest_risk_params(
    db: AsyncSession,
    assets: list[str] | None = None,
    protocols: list[str] | None = None,
) -> list[ProtocolRiskParamsSnapshot]:
    """
    Return the most recent row per (protocol, chain, asset, debt_asset) subject
    to optional asset and protocol filters.
    """
    # Build subquery for max snapshot_at per natural key
    sub_q = select(
        ProtocolRiskParamsSnapshot.protocol,
        ProtocolRiskParamsSnapshot.chain,
        ProtocolRiskParamsSnapshot.asset,
        ProtocolRiskParamsSnapshot.debt_asset,
        func.max(ProtocolRiskParamsSnapshot.snapshot_at).label("max_at"),
    ).group_by(
        ProtocolRiskParamsSnapshot.protocol,
        ProtocolRiskParamsSnapshot.chain,
        ProtocolRiskParamsSnapshot.asset,
        ProtocolRiskParamsSnapshot.debt_asset,
    )

    if assets:
        assets_upper = [a.upper() for a in assets]
        sub_q = sub_q.where(ProtocolRiskParamsSnapshot.asset.in_(assets_upper))
    if protocols:
        sub_q = sub_q.where(ProtocolRiskParamsSnapshot.protocol.in_(protocols))

    sub = sub_q.subquery()

    stmt = select(ProtocolRiskParamsSnapshot).join(
        sub,
        (ProtocolRiskParamsSnapshot.protocol == sub.c.protocol)
        & (ProtocolRiskParamsSnapshot.chain == sub.c.chain)
        & (ProtocolRiskParamsSnapshot.asset == sub.c.asset)
        & (
            (ProtocolRiskParamsSnapshot.debt_asset == sub.c.debt_asset)
            | (
                ProtocolRiskParamsSnapshot.debt_asset.is_(None)
                & sub.c.debt_asset.is_(None)
            )
        )
        & (ProtocolRiskParamsSnapshot.snapshot_at == sub.c.max_at),
    )

    result = await db.execute(stmt)
    return list(result.scalars().all())
