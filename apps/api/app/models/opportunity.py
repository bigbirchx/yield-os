"""
SQLAlchemy models for the unified opportunity storage layer.

- MarketOpportunityRow   — latest state per opportunity (upserted on each ingestion)
- MarketOpportunitySnapshotRow — time-series snapshots for rate history charts
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class MarketOpportunityRow(Base):
    """Current state of a single yield opportunity — upserted by ingestion."""

    __tablename__ = "market_opportunities"

    # Identity
    opportunity_id: Mapped[str] = mapped_column(String(512), primary_key=True)
    venue: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    chain: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    protocol: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    protocol_slug: Mapped[str] = mapped_column(String(128), nullable=False)
    market_id: Mapped[str] = mapped_column(String(512), nullable=False)
    market_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    side: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    asset_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    asset_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    umbrella_group: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    asset_sub_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    opportunity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    effective_duration: Mapped[str] = mapped_column(String(32), nullable=False)
    maturity_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    days_to_maturity: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Yield
    total_apy_pct: Mapped[float] = mapped_column(Float, nullable=False)
    base_apy_pct: Mapped[float] = mapped_column(Float, nullable=False)
    reward_breakdown: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")

    # Size and capacity
    total_supplied: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_supplied_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_borrowed: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_borrowed_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    capacity_cap: Mapped[float | None] = mapped_column(Float, nullable=True)
    capacity_remaining: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_capacity_capped: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    tvl_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Liquidity
    liquidity: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")

    # Rate model
    rate_model: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Collateral (supply side)
    is_collateral_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    as_collateral_max_ltv_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    as_collateral_liquidation_ltv_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Collateral matrix (borrow side)
    collateral_options: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Receipt token
    receipt_token: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Filtering tags
    is_amm_lp: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_pendle: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    pendle_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")

    # Metadata
    data_source: Mapped[str] = mapped_column(String(128), nullable=False)
    last_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    data_freshness_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    source_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class MarketOpportunitySnapshotRow(Base):
    """Time-series rate snapshot for a single opportunity."""

    __tablename__ = "market_opportunity_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[str] = mapped_column(
        String(512),
        ForeignKey("market_opportunities.opportunity_id", ondelete="CASCADE"),
        nullable=False,
    )
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    total_apy_pct: Mapped[float] = mapped_column(Float, nullable=False)
    base_apy_pct: Mapped[float] = mapped_column(Float, nullable=False)
    total_supplied: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_supplied_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_borrowed: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_borrowed_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    utilization_rate_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    tvl_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
