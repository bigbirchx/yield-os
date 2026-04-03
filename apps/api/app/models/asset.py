from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Asset(Base):
    """Legacy lightweight asset table — kept for backwards compatibility."""

    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    family: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# Asset ontology tables — synced from the in-memory asset-registry package
# ---------------------------------------------------------------------------


class AssetDefinitionRow(Base):
    """
    Mirrors packages/asset-registry ASSET_REGISTRY.

    canonical_id is the PK (e.g. "USDC", "WSTETH") — no surrogate key needed
    because the registry already has stable, unique string identifiers.
    """

    __tablename__ = "asset_definitions"

    canonical_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    umbrella: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    sub_type: Mapped[str] = mapped_column(String(64), nullable=False)
    fungibility: Mapped[str] = mapped_column(String(32), nullable=False)
    coingecko_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    # Self-referential FK — DEFERRABLE so bulk upserts land in one transaction
    underlying_asset_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("asset_definitions.canonical_id", deferrable=True, initially="DEFERRED"),
        nullable=True,
    )
    tags: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class AssetChainDeployment(Base):
    """
    One row per (asset, chain) pair from AssetDefinition.native_chains +
    decimals_by_chain.  is_native=True when the chain is in native_chains.
    """

    __tablename__ = "asset_chain_deployments"
    __table_args__ = (
        UniqueConstraint("canonical_id", "chain", name="uq_asset_chain"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("asset_definitions.canonical_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    contract_address: Mapped[str | None] = mapped_column(String(256), nullable=True)
    decimals: Mapped[int] = mapped_column(Integer, nullable=False, default=18)
    is_native: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class VenueSymbolMapping(Base):
    """
    Mirrors packages/asset-registry VENUE_MAPPINGS.

    chain is stored as '' (empty string) rather than NULL so the unique
    constraint (venue, venue_symbol, chain) works correctly — SQL NULL != NULL.
    """

    __tablename__ = "venue_symbol_mappings"
    __table_args__ = (
        UniqueConstraint("venue", "venue_symbol", "chain", name="uq_venue_symbol_chain"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    venue: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    venue_symbol: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("asset_definitions.canonical_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Empty string means chain-agnostic; never NULL (see table_args note above)
    chain: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    is_contract_address: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ConversionEdgeRow(Base):
    """
    Mirrors packages/asset-registry CONVERSION_GRAPH.

    (from_asset, to_asset, method, chain) is the natural unique key.
    """

    __tablename__ = "conversion_edges"
    __table_args__ = (
        UniqueConstraint(
            "from_asset", "to_asset", "method", "chain",
            name="uq_conversion_edge",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    from_asset: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("asset_definitions.canonical_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    to_asset: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("asset_definitions.canonical_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    protocol: Mapped[str | None] = mapped_column(String(128), nullable=True)
    estimated_gas_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    fee_bps: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    slippage_bps_estimate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    min_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_deterministic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    capacity_limited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
