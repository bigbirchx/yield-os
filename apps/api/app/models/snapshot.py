from datetime import datetime

from sqlalchemy import DateTime, Float, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DerivativesSnapshot(Base):
    """One row per (symbol, venue, snapshot_at)."""

    __tablename__ = "derivatives_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    venue: Mapped[str] = mapped_column(String(64), nullable=False)
    funding_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    open_interest_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    basis_annualized: Mapped[float | None] = mapped_column(Float, nullable=True)
    mark_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    index_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    spot_volume_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    perp_volume_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LendingMarketSnapshot(Base):
    """One row per (symbol, protocol, market, snapshot_at)."""

    __tablename__ = "lending_market_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    protocol: Mapped[str] = mapped_column(String(64), nullable=False)
    market: Mapped[str] = mapped_column(String(128), nullable=False)
    supply_apy: Mapped[float | None] = mapped_column(Float, nullable=True)
    borrow_apy: Mapped[float | None] = mapped_column(Float, nullable=True)
    utilization: Mapped[float | None] = mapped_column(Float, nullable=True)
    tvl_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    available_liquidity_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    borrow_cap_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    supply_cap_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
