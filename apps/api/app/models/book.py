"""
SQLAlchemy models for the portfolio book layer.

- BookRow               — imported book metadata
- BookPositionRow       — individual positions (loans/deployments)
- BookObservedCollateralRow — counterparty-level collateral observations
- BookCollateralAllocationRow — pro-rata allocation of collateral to loans
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BookRow(Base):
    """An imported book snapshot."""

    __tablename__ = "books"

    book_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    source_file: Mapped[str] = mapped_column(String(512), nullable=False)
    import_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    as_of_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    total_positions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class BookPositionRow(Base):
    """A single position from the CreditDesk book."""

    __tablename__ = "book_positions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id"],
            ["books.book_id"],
            ondelete="CASCADE",
        ),
    )

    book_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    loan_id: Mapped[int] = mapped_column(Integer, primary_key=True)

    customer_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    counterparty_name: Mapped[str] = mapped_column(String(256), nullable=False)
    counterparty_legal_entity: Mapped[str | None] = mapped_column(
        String(256), nullable=True
    )
    category: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    # Principal
    principal_asset: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    principal_qty: Mapped[float] = mapped_column(Float, nullable=False)
    principal_usd: Mapped[float] = mapped_column(Float, nullable=False)

    # Dates / tenor
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    maturity_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    tenor: Mapped[str] = mapped_column(String(16), nullable=False)
    recall_period_days: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Collateral terms
    collateral_assets_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    initial_collateralization_ratio_pct: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    rehypothecation_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    collateral_substitution_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    is_collateralized: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    # Loan terms
    loan_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    interest_rate_pct: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    query_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Derived
    protocol_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    protocol_chain: Mapped[str | None] = mapped_column(String(32), nullable=True)
    umbrella_group: Mapped[str | None] = mapped_column(String(32), nullable=True)
    matched_opportunity_id: Mapped[str | None] = mapped_column(
        String(512), nullable=True
    )
    current_market_rate_pct: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    rate_vs_market_bps: Mapped[float | None] = mapped_column(Float, nullable=True)


class BookObservedCollateralRow(Base):
    """Observed collateral holding at the counterparty level."""

    __tablename__ = "book_observed_collateral"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id"],
            ["books.book_id"],
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    book_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    asof_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    counterparty_name: Mapped[str] = mapped_column(String(256), nullable=False)
    collateral_relationship: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    collateral_asset: Mapped[str] = mapped_column(String(64), nullable=False)
    units_posted: Mapped[float] = mapped_column(Float, nullable=False)
    data_source: Mapped[str] = mapped_column(String(128), nullable=False)
    is_tri_party: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    custodial_venue: Mapped[str] = mapped_column(String(128), nullable=False)


class BookCollateralAllocationRow(Base):
    """Pro-rata allocation of observed collateral to individual loans."""

    __tablename__ = "book_collateral_allocations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id"],
            ["books.book_id"],
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    book_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    loan_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    collateral_asset: Mapped[str] = mapped_column(String(64), nullable=False)
    allocated_units: Mapped[float] = mapped_column(Float, nullable=False)
    allocated_usd: Mapped[float] = mapped_column(Float, nullable=False)
    allocation_weight_pct: Mapped[float] = mapped_column(Float, nullable=False)
