from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.route_optimizer import (
    Bottleneck,
    CostComponent,
    Route,
    RouteAssumptions,
    RouteOptimizerResult,
    optimize,
)
from app.services.route_optimizer_loader import load_inputs

router = APIRouter(prefix="/api/assets", tags=["route-optimizer"])


# ─────────────────────────────────────────────────────────────────────────────
# Request schema
# ─────────────────────────────────────────────────────────────────────────────


class AssumptionsOverride(BaseModel):
    """All fields are optional — only provided values are applied."""

    max_pool_share: float | None = None
    max_oi_share: float | None = None
    spot_slippage_bps: float | None = None
    funding_variance_premium_bps: float | None = None
    wrapper_extra_slippage_bps: float | None = None
    unbonding_bps_per_day: float | None = None
    size_shortfall_penalty_bps: float | None = None


class RouteOptimizerRequest(BaseModel):
    request_size_usd: float = Field(
        default=10_000_000,
        gt=0,
        description="Desired notional in USD to source.",
    )
    assumptions_override: AssumptionsOverride = Field(
        default_factory=AssumptionsOverride,
        description="Override specific assumption defaults for this request.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Response schemas
# ─────────────────────────────────────────────────────────────────────────────


class CostComponentOut(BaseModel):
    name: str
    value_bps: float
    source: str
    is_assumption: bool


class BottleneckOut(BaseModel):
    constraint: str
    limiting_factor: str
    severity: Literal["hard", "soft"]
    value: float | None
    value_unit: str


class RouteOut(BaseModel):
    route_type: str
    display_name: str
    description: str
    rank: int
    total_cost_bps: float
    effective_cost_bps: float
    max_executable_usd: float
    feasible: bool
    cost_components: list[CostComponentOut]
    bottlenecks: list[BottleneckOut]
    assumptions_used: list[str]
    ranking_rationale: str


class RouteAssumptionsOut(BaseModel):
    max_pool_share: float
    max_oi_share: float
    spot_slippage_bps: float
    funding_variance_premium_bps: float
    wrapper_extra_slippage_bps: float
    unbonding_bps_per_day: float
    size_shortfall_penalty_bps: float


class RouteOptimizerOut(BaseModel):
    target_asset: str
    request_size_usd: float
    recommended_route: str
    summary: str
    routes: list[RouteOut]
    computed_at: datetime
    assumptions: RouteAssumptionsOut


def _to_out(result: RouteOptimizerResult) -> RouteOptimizerOut:
    ass = result.assumptions
    return RouteOptimizerOut(
        target_asset=result.target_asset,
        request_size_usd=result.request_size_usd,
        recommended_route=result.recommended_route,
        summary=result.summary,
        computed_at=result.computed_at,
        assumptions=RouteAssumptionsOut(
            max_pool_share=ass.max_pool_share,
            max_oi_share=ass.max_oi_share,
            spot_slippage_bps=ass.spot_slippage_bps,
            funding_variance_premium_bps=ass.funding_variance_premium_bps,
            wrapper_extra_slippage_bps=ass.wrapper_extra_slippage_bps,
            unbonding_bps_per_day=ass.unbonding_bps_per_day,
            size_shortfall_penalty_bps=ass.size_shortfall_penalty_bps,
        ),
        routes=[
            RouteOut(
                route_type=r.route_type,
                display_name=r.display_name,
                description=r.description,
                rank=r.rank,
                total_cost_bps=r.total_cost_bps,
                effective_cost_bps=r.effective_cost_bps,
                max_executable_usd=r.max_executable_usd,
                feasible=r.feasible,
                cost_components=[
                    CostComponentOut(
                        name=c.name,
                        value_bps=c.value_bps,
                        source=c.source,
                        is_assumption=c.is_assumption,
                    )
                    for c in r.cost_components
                ],
                bottlenecks=[
                    BottleneckOut(
                        constraint=b.constraint,
                        limiting_factor=b.limiting_factor,
                        severity=b.severity,
                        value=b.value,
                        value_unit=b.value_unit,
                    )
                    for b in r.bottlenecks
                ],
                assumptions_used=r.assumptions_used,
                ranking_rationale=r.ranking_rationale,
            )
            for r in result.routes
        ],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/{symbol}/route-optimizer", response_model=RouteOptimizerOut)
async def route_optimizer(
    symbol: str,
    body: RouteOptimizerRequest = RouteOptimizerRequest(),
    db: AsyncSession = Depends(get_db),
):
    """
    Compares four sourcing strategies for the requested asset and returns
    them ranked by annualized cost.

    Routes evaluated:
    - **direct_borrow**: borrow the asset directly from the cheapest lending market
    - **stable_borrow_spot**: borrow stablecoin, purchase asset on spot
    - **wrapper_transform**: borrow a related asset and convert (wrap/stake/unwrap)
    - **synthetic_hedge**: borrow stablecoin + hold long perp (synthetic exposure)

    All assumptions are explicit and included in the response. Override them via
    `assumptions_override` to run desk-specific scenarios.

    Sources: DeFiLlama (lending markets), Velo (derivatives), static transform metadata
    """
    overrides = {
        k: v
        for k, v in body.assumptions_override.model_dump().items()
        if v is not None
    }
    inputs = await load_inputs(
        db,
        symbol=symbol,
        request_size_usd=body.request_size_usd,
        assumptions_override=overrides if overrides else None,
    )
    result = optimize(inputs)
    return _to_out(result)
