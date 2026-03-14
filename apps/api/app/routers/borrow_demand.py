from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.borrow_demand import (
    BorrowDemandAnalysis,
    EventOverlay,
    ReasonFactor,
    analyze,
)
from app.services.borrow_demand_loader import load_inputs

router = APIRouter(prefix="/api/assets", tags=["borrow-demand"])


# ─────────────────────────────────────────────────────────────────────────────
# Response schemas
# ─────────────────────────────────────────────────────────────────────────────


class ReasonFactorOut(BaseModel):
    name: str
    display_label: str
    direction: Literal["elevates", "suppresses", "neutral"]
    score: float
    value: float | None
    baseline: float | None
    value_unit: str
    metric_source: str
    metric_name: str
    snapshot_at: datetime | None
    evidence_note: str


class EventOverlayOut(BaseModel):
    label: str
    event_date: datetime
    impact: Literal["elevates", "suppresses", "neutral"]
    source: str
    notes: str


class BorrowDemandOut(BaseModel):
    symbol: str
    demand_level: Literal["elevated", "normal", "suppressed"]
    demand_score: float
    confidence: float
    reasons: list[ReasonFactorOut]
    explanation: str
    computed_at: datetime
    data_window_days: int
    event_overlays: list[EventOverlayOut]


def _analysis_to_out(analysis: BorrowDemandAnalysis) -> BorrowDemandOut:
    return BorrowDemandOut(
        symbol=analysis.symbol,
        demand_level=analysis.demand_level,
        demand_score=analysis.demand_score,
        confidence=analysis.confidence,
        reasons=[
            ReasonFactorOut(
                name=f.name,
                display_label=f.display_label,
                direction=f.direction,
                score=f.score,
                value=f.value,
                baseline=f.baseline,
                value_unit=f.value_unit,
                metric_source=f.metric_source,
                metric_name=f.metric_name,
                snapshot_at=f.snapshot_at,
                evidence_note=f.evidence_note,
            )
            for f in analysis.reasons
        ],
        explanation=analysis.explanation,
        computed_at=analysis.computed_at,
        data_window_days=analysis.data_window_days,
        event_overlays=[
            EventOverlayOut(
                label=e.label,
                event_date=e.event_date,
                impact=e.impact,
                source=e.source,
                notes=e.notes,
            )
            for e in analysis.event_overlays
        ],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/{symbol}/borrow-demand", response_model=BorrowDemandOut)
async def borrow_demand(
    symbol: str,
    days: int = Query(default=30, ge=1, le=90, description="Lookback window for history"),
    db: AsyncSession = Depends(get_db),
):
    """
    Runs the borrow-demand explanation engine for the requested asset.

    Returns:
    - **demand_level**: elevated | normal | suppressed
    - **demand_score**: weighted composite score (positive = elevating factors dominate)
    - **confidence**: data coverage × freshness quality (0–1)
    - **reasons**: per-factor scores with values, baselines, and evidence notes
    - **explanation**: 3–5 sentence traceable narrative for desk display
    - **event_overlays**: manual annotations affecting demand interpretation

    Sources: Velo (derivatives), DeFiLlama (lending / staking)
    """
    inputs = await load_inputs(db, symbol=symbol, days=days)
    analysis = analyze(inputs)
    return _analysis_to_out(analysis)
