"""
SQLAlchemy model for the token_universe table.

Stores the merged view of the static ASSET_REGISTRY plus CoinGecko top-500
tokens.  Populated and refreshed by TokenUniverseService.sync_to_db().
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TokenUniverseRow(Base):
    """One row per canonical token in the merged token universe."""

    __tablename__ = "token_universe"

    canonical_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    coingecko_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    umbrella: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    sub_type: Mapped[str] = mapped_column(String(64), nullable=False)
    market_cap_rank: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    market_cap_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_price_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    chains: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    is_static: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false", index=True)
    last_refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
