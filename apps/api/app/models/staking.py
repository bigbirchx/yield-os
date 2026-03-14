from datetime import datetime

from sqlalchemy import DateTime, Float, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class StakingSnapshot(Base):
    """
    One row per (symbol, protocol, chain, snapshot_at).

    Covers native staking and LST/LSD tokens (stETH, cbETH, rETH, mSOL, etc.).
    """

    __tablename__ = "staking_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    underlying_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    protocol: Mapped[str] = mapped_column(String(64), nullable=False)
    chain: Mapped[str] = mapped_column(String(64), nullable=False)
    pool_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    staking_apy: Mapped[float | None] = mapped_column(Float, nullable=True)
    base_apy: Mapped[float | None] = mapped_column(Float, nullable=True)
    reward_apy: Mapped[float | None] = mapped_column(Float, nullable=True)
    tvl_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
