from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ProtocolRiskParamsSnapshot(Base):
    """
    One row per (protocol, chain, asset, [debt_asset], snapshot_at).

    Captures collateral risk parameters directly from Aave, Morpho Blue, and
    Kamino. For isolated-pair protocols (Morpho Blue), both asset and
    debt_asset are populated; for pool-based protocols (Aave, Kamino) only
    asset is set.

    All numeric thresholds are stored as decimals (0.80 = 80%), not basis
    points, regardless of how the source protocol encodes them.
    """

    __tablename__ = "protocol_risk_params_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    protocol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    chain: Mapped[str] = mapped_column(String(64), nullable=False)
    asset: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    debt_asset: Mapped[str | None] = mapped_column(String(32), nullable=True)
    market_address: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Risk thresholds — stored as decimals regardless of source encoding
    max_ltv: Mapped[float | None] = mapped_column(Float, nullable=True)
    liquidation_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    liquidation_penalty: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Capacity — native token units where USD conversion is not provided by source
    borrow_cap_native: Mapped[float | None] = mapped_column(Float, nullable=True)
    supply_cap_native: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Eligibility flags
    collateral_eligible: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    borrowing_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_active: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Available capacity (from source where provided)
    available_capacity_native: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Raw source payload for reconciliation
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
