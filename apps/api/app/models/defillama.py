"""
SQLAlchemy ORM models for the DefiLlama free-tier data layer.

All tables use source_name = 'defillama_free' for traceability.
Raw payloads are preserved verbatim in JSON columns.
"""

from __future__ import annotations
from datetime import datetime
from sqlalchemy import DateTime, Float, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base

_SOURCE = "defillama_free"


class DLYieldPoolSnapshot(Base):
    __tablename__ = "defillama_yield_pool_snapshot"
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    pool_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    project: Mapped[str] = mapped_column(String(128), nullable=False)
    chain: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    tvl_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    apy: Mapped[float | None] = mapped_column(Float, nullable=True)
    apy_base: Mapped[float | None] = mapped_column(Float, nullable=True)
    apy_reward: Mapped[float | None] = mapped_column(Float, nullable=True)
    stablecoin: Mapped[bool | None] = mapped_column(nullable=True)
    il_risk: Mapped[str | None] = mapped_column(String(32), nullable=True)
    exposure: Mapped[str | None] = mapped_column(String(64), nullable=True)
    predictions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_name: Mapped[str] = mapped_column(String(64), nullable=False, default=_SOURCE)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DLYieldPoolHistory(Base):
    __tablename__ = "defillama_yield_pool_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    pool_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    apy: Mapped[float | None] = mapped_column(Float, nullable=True)
    tvl_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    apy_base: Mapped[float | None] = mapped_column(Float, nullable=True)
    apy_reward: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_name: Mapped[str] = mapped_column(String(64), nullable=False, default=_SOURCE)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DLProtocolSnapshot(Base):
    __tablename__ = "defillama_protocol_snapshot"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    protocol_slug: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    protocol_name: Mapped[str] = mapped_column(String(256), nullable=False)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chain: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tvl_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    change_1d: Mapped[float | None] = mapped_column(Float, nullable=True)
    change_7d: Mapped[float | None] = mapped_column(Float, nullable=True)
    change_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_name: Mapped[str] = mapped_column(String(64), nullable=False, default=_SOURCE)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DLChainTvlHistory(Base):
    __tablename__ = "defillama_chain_tvl_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    chain: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    tvl_usd: Mapped[float] = mapped_column(Float, nullable=False)
    source_name: Mapped[str] = mapped_column(String(64), nullable=False, default=_SOURCE)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DLStablecoinSnapshot(Base):
    __tablename__ = "defillama_stablecoin_snapshot"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    stablecoin_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    circulating_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    chains: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    peg_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    peg_mechanism: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_name: Mapped[str] = mapped_column(String(64), nullable=False, default=_SOURCE)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DLStablecoinHistory(Base):
    __tablename__ = "defillama_stablecoin_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    chain: Mapped[str | None] = mapped_column(String(64), nullable=True)
    circulating_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_name: Mapped[str] = mapped_column(String(64), nullable=False, default=_SOURCE)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DLMarketContextSnapshot(Base):
    """DEX volume, open-interest, and fees context.
    context_type: 'dex_volume' | 'open_interest' | 'fees_revenue'
    """
    __tablename__ = "defillama_market_context_snapshot"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    context_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    protocol_or_chain: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    metric_name: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_name: Mapped[str] = mapped_column(String(64), nullable=False, default=_SOURCE)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
