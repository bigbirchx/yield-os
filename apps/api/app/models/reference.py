"""
SQLAlchemy models for the CoinGecko market-reference layer.

These tables serve as the canonical asset metadata and market context store.
They are NOT used for protocol-native lending parameters or derivatives routing.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AssetReferenceMap(Base):
    """
    Canonical asset registry — one row per (symbol, source_name) pair.

    Combines static metadata (coingecko_id, contract_address, chain) with
    the normalised symbol we use everywhere else in Yield OS.  Updated on
    each asset-discovery ingestion run (daily or ad hoc).
    """

    __tablename__ = "asset_reference_map"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    canonical_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    coingecko_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    asset_type: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # crypto | stablecoin | wrapper | lst
    chain: Mapped[str | None] = mapped_column(String(64), nullable=True)
    contract_address: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source_name: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("symbol", "source_name", name="uq_asset_ref_symbol_source"),
    )


class MarketReferenceSnapshot(Base):
    """
    Periodic market snapshot — one row per (coingecko_id, snapshot_at).

    Captures current price, market cap, volume, and supply metrics.
    Ingested every 15 minutes for tracked assets.
    """

    __tablename__ = "market_reference_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    coingecko_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    current_price_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_cap_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    fully_diluted_valuation_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_24h_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    circulating_supply: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_supply: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_supply: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_change_24h_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_name: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MarketReferenceHistory(Base):
    """
    Daily price / market cap / volume history per asset.

    Populated by the backfill job from /coins/{id}/market_chart.
    One row per (coingecko_id, snapshot_at day).
    """

    __tablename__ = "market_reference_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    coingecko_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    price_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_cap_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_24h_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_name: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_mkt_hist_coin_at", "coingecko_id", "snapshot_at"),
    )


class ApiUsageSnapshot(Base):
    """
    CoinGecko API usage monitoring — one row per ingestion run.

    Populated every 30 minutes from /key (Pro endpoint).
    Allows tracking credit burn rate and estimating remaining budget.
    """

    __tablename__ = "api_usage_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    rate_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    remaining_credits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monthly_total_credits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
