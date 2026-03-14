from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.repositories.risk import get_latest_risk_params

router = APIRouter(prefix="/api/lending", tags=["risk"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class LtvMatrixRequest(BaseModel):
    """Optional filters. Omit to get all available rows."""

    assets: list[str] | None = None
    protocols: list[str] | None = None


class RiskParamsOut(BaseModel):
    protocol: str
    chain: str
    asset: str
    debt_asset: str | None
    market_address: str | None
    max_ltv: float | None
    liquidation_threshold: float | None
    liquidation_penalty: float | None
    borrow_cap_native: float | None
    supply_cap_native: float | None
    collateral_eligible: bool | None
    borrowing_enabled: bool | None
    is_active: bool | None
    available_capacity_native: float | None
    snapshot_at: datetime
    ingested_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/ltv-matrix", response_model=list[RiskParamsOut])
async def ltv_matrix(
    body: LtvMatrixRequest = LtvMatrixRequest(),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns the latest risk parameter snapshot per (protocol, chain, asset)
    across Aave v3, Morpho Blue, and Kamino.

    Supports optional filtering by asset symbols and protocol names.

    Sources: direct protocol connectors (Aave subgraph, Morpho Blue API, Kamino API)
    """
    rows = await get_latest_risk_params(
        db,
        assets=body.assets,
        protocols=body.protocols,
    )
    return [RiskParamsOut.model_validate(r) for r in rows]


@router.get("/risk-params/{asset}", response_model=list[RiskParamsOut])
async def risk_params_by_asset(
    asset: str,
    db: AsyncSession = Depends(get_db),
):
    """Returns the latest risk params for a single asset across all protocols."""
    rows = await get_latest_risk_params(db, assets=[asset])
    return [RiskParamsOut.model_validate(r) for r in rows]
