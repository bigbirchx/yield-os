"""
Yield route optimizer endpoints.

Uses the route-optimizer package to find, rank, and compare yield routes
across all opportunities considering conversion costs, capacity, rate
impact, and risk.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.opportunity import MarketOpportunityRow

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/optimizer", tags=["yield-optimizer"])


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class OptimizerConfig(BaseModel):
    exclude_amm_lp: bool = True
    exclude_pendle: bool = False
    include_borrow_routes: bool = True
    max_ltv_pct: float = 70.0
    risk_tolerance: Literal["conservative", "moderate", "aggressive"] = "moderate"
    preferred_chains: list[str] | None = None
    max_conversion_steps: int = 3
    min_tvl_usd: float = 100_000.0


class RouteRequest(BaseModel):
    entry_asset: str = Field(..., description="Asset currently held (e.g. ETH, USDC)")
    entry_amount_usd: float = Field(..., gt=0, description="Position size in USD")
    holding_period_days: int = Field(default=90, ge=1, le=365)
    config: OptimizerConfig = Field(default_factory=OptimizerConfig)


class CompareEntry(BaseModel):
    entry_asset: str
    entry_amount_usd: float = Field(..., gt=0)


class CompareRequest(BaseModel):
    routes: list[CompareEntry] = Field(..., min_length=1, max_length=5)
    holding_period_days: int = Field(default=90, ge=1, le=365)
    config: OptimizerConfig = Field(default_factory=OptimizerConfig)


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class ConversionStepOut(BaseModel):
    from_asset: str
    to_asset: str
    method: str
    chain: str
    protocol: str | None
    fee_bps: float
    slippage_bps: float
    gas_usd: float


class CollateralOut(BaseModel):
    collateral_asset: str
    collateral_amount_usd: float
    max_ltv_pct: float
    liquidation_ltv_pct: float
    liquidation_buffer_pct: float
    conversion_cost_bps: float
    opportunity_cost_apy_pct: float


class YieldRouteOut(BaseModel):
    # Identity
    opportunity_id: str
    venue: str
    chain: str
    protocol: str
    market_name: str | None
    side: str
    target_asset: str
    umbrella_group: str
    opportunity_type: str

    # Conversion
    conversion_steps: list[ConversionStepOut]
    conversion_cost_bps: float
    conversion_gas_usd: float
    conversion_time_seconds: list[int]  # [min, max]
    is_conversion_deterministic: bool

    # Yield
    gross_apy_pct: float
    net_apy_pct: float
    annualized_conversion_cost_pct: float

    # Capacity
    max_deployable_usd: float
    capacity_limited: bool
    tvl_usd: float | None

    # Rate impact
    rate_impact_bps: float
    post_deposit_apy_pct: float | None

    # Risk
    risk_flags: list[str]
    risk_score: float

    # Collateral
    collateral: CollateralOut | None

    # Metadata
    computed_at: str


class RouteResponse(BaseModel):
    entry_asset: str
    entry_amount_usd: float
    holding_period_days: int
    total_routes: int
    routes: list[YieldRouteOut]
    best_supply_route: YieldRouteOut | None
    best_borrow_route: YieldRouteOut | None
    computed_at: str


class CompareResponse(BaseModel):
    comparisons: list[RouteResponse]
    computed_at: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RISK_TOLERANCE_MAP = {
    "conservative": 0.3,
    "moderate": 0.5,
    "aggressive": 0.8,
}


def _build_optimizer(
    rows: list[MarketOpportunityRow],
    req_config: OptimizerConfig,
    holding_period_days: int,
):
    """Build a RouteOptimizer from DB rows and request config."""
    from route_optimizer import RouteOptimizer, RouteOptimizerConfig
    from opportunity_schema.schema import (
        CollateralAssetInfo,
        EffectiveDuration,
        LiquidityInfo,
        MarketOpportunity,
        OpportunitySide,
        OpportunityType,
        RateModelInfo,
        ReceiptTokenInfo,
        RewardBreakdown,
    )

    opportunities: list[MarketOpportunity] = []

    for row in rows:
        try:
            # Parse sub-models from JSONB
            liq_data = row.liquidity or {}
            liquidity = LiquidityInfo(**liq_data) if liq_data else LiquidityInfo()

            rate_model = None
            if row.rate_model:
                rate_model = RateModelInfo(**row.rate_model)

            collateral_options = None
            if row.collateral_options:
                collateral_options = [
                    CollateralAssetInfo(**c) for c in row.collateral_options
                ]

            receipt_token = None
            if row.receipt_token:
                receipt_token = ReceiptTokenInfo(**row.receipt_token)

            reward_breakdown = []
            if row.reward_breakdown:
                for rb in row.reward_breakdown:
                    reward_breakdown.append(RewardBreakdown(
                        reward_type=rb.get("reward_type", "NATIVE_YIELD"),
                        token_id=rb.get("token_id"),
                        token_name=rb.get("token_name"),
                        apy_pct=rb.get("apy_pct", 0.0),
                        is_variable=rb.get("is_variable", True),
                        notes=rb.get("notes"),
                    ))

            opp = MarketOpportunity(
                opportunity_id=row.opportunity_id,
                venue=row.venue,
                chain=row.chain,
                protocol=row.protocol,
                protocol_slug=row.protocol_slug,
                market_id=row.market_id,
                market_name=row.market_name,
                side=OpportunitySide(row.side),
                asset_id=row.asset_id,
                asset_symbol=row.asset_symbol,
                umbrella_group=row.umbrella_group,
                asset_sub_type=row.asset_sub_type,
                opportunity_type=OpportunityType(row.opportunity_type),
                effective_duration=EffectiveDuration(row.effective_duration),
                maturity_date=row.maturity_date,
                days_to_maturity=row.days_to_maturity,
                total_apy_pct=row.total_apy_pct,
                base_apy_pct=row.base_apy_pct,
                reward_breakdown=reward_breakdown,
                total_supplied=row.total_supplied,
                total_supplied_usd=row.total_supplied_usd,
                total_borrowed=row.total_borrowed,
                total_borrowed_usd=row.total_borrowed_usd,
                capacity_cap=row.capacity_cap,
                capacity_remaining=row.capacity_remaining,
                is_capacity_capped=row.is_capacity_capped,
                tvl_usd=row.tvl_usd,
                liquidity=liquidity,
                rate_model=rate_model,
                is_collateral_eligible=row.is_collateral_eligible,
                as_collateral_max_ltv_pct=row.as_collateral_max_ltv_pct,
                as_collateral_liquidation_ltv_pct=row.as_collateral_liquidation_ltv_pct,
                collateral_options=collateral_options,
                receipt_token=receipt_token,
                is_amm_lp=row.is_amm_lp,
                is_pendle=row.is_pendle,
                pendle_type=row.pendle_type,
                tags=row.tags or [],
                data_source=row.data_source,
                last_updated_at=row.last_updated_at,
                data_freshness_seconds=row.data_freshness_seconds,
                source_url=row.source_url,
            )
            opportunities.append(opp)
        except Exception:
            log.debug("skip_row_conversion", opp_id=row.opportunity_id, exc_info=True)
            continue

    config = RouteOptimizerConfig(
        holding_period_days=holding_period_days,
        max_conversion_steps=req_config.max_conversion_steps,
        min_tvl_usd=req_config.min_tvl_usd,
        exclude_amm_lp=req_config.exclude_amm_lp,
        exclude_pendle=req_config.exclude_pendle,
        risk_tolerance=_RISK_TOLERANCE_MAP.get(req_config.risk_tolerance, 0.5),
        max_risk_score=_RISK_TOLERANCE_MAP.get(req_config.risk_tolerance, 0.5) + 0.3,
    )

    return RouteOptimizer(opportunities, config=config)


def _route_to_out(route) -> YieldRouteOut:
    """Convert a YieldRoute dataclass to the API response model."""
    steps = [
        ConversionStepOut(
            from_asset=edge.from_asset,
            to_asset=edge.to_asset,
            method=edge.method.value if hasattr(edge.method, "value") else str(edge.method),
            chain=edge.chain.value if hasattr(edge.chain, "value") else str(edge.chain),
            protocol=edge.protocol,
            fee_bps=edge.fee_bps,
            slippage_bps=edge.slippage_bps_estimate,
            gas_usd=edge.estimated_gas_usd,
        )
        for edge in route.conversion_path
    ]

    collateral = None
    if route.collateral:
        c = route.collateral
        collateral = CollateralOut(
            collateral_asset=c.collateral_asset,
            collateral_amount_usd=c.collateral_amount_usd,
            max_ltv_pct=c.max_ltv_pct,
            liquidation_ltv_pct=c.liquidation_ltv_pct,
            liquidation_buffer_pct=c.liquidation_buffer_pct,
            conversion_cost_bps=c.conversion_cost_bps,
            opportunity_cost_apy_pct=c.opportunity_cost_apy_pct,
        )

    opp = route.opportunity
    return YieldRouteOut(
        opportunity_id=opp.opportunity_id,
        venue=opp.venue,
        chain=opp.chain,
        protocol=opp.protocol,
        market_name=opp.market_name,
        side=opp.side.value if hasattr(opp.side, "value") else str(opp.side),
        target_asset=route.target_asset,
        umbrella_group=opp.umbrella_group,
        opportunity_type=opp.opportunity_type.value if hasattr(opp.opportunity_type, "value") else str(opp.opportunity_type),
        conversion_steps=steps,
        conversion_cost_bps=round(route.conversion_cost_bps, 2),
        conversion_gas_usd=round(route.conversion_gas_usd, 2),
        conversion_time_seconds=[
            route.conversion_time_min_seconds,
            route.conversion_time_max_seconds,
        ],
        is_conversion_deterministic=route.is_conversion_deterministic,
        gross_apy_pct=round(route.gross_apy_pct, 4),
        net_apy_pct=round(route.net_apy_pct, 4),
        annualized_conversion_cost_pct=round(route.annualized_conversion_cost_pct, 4),
        max_deployable_usd=round(route.max_deployable_usd, 2),
        capacity_limited=route.capacity_limited,
        tvl_usd=opp.tvl_usd,
        rate_impact_bps=round(route.rate_impact_bps, 2),
        post_deposit_apy_pct=round(route.post_deposit_apy_pct, 4) if route.post_deposit_apy_pct is not None else None,
        risk_flags=route.risk_flags,
        risk_score=round(route.risk_score, 4),
        collateral=collateral,
        computed_at=route.computed_at.isoformat(),
    )


async def _load_rows(
    db: AsyncSession,
    config: OptimizerConfig,
) -> list[MarketOpportunityRow]:
    """Load opportunity rows from DB with pre-filtering."""
    q = select(MarketOpportunityRow)

    if config.exclude_amm_lp:
        q = q.where(MarketOpportunityRow.is_amm_lp == False)  # noqa: E712
    if config.exclude_pendle:
        q = q.where(MarketOpportunityRow.is_pendle == False)  # noqa: E712
    if config.min_tvl_usd > 0:
        q = q.where(
            (MarketOpportunityRow.tvl_usd >= config.min_tvl_usd)
            | (MarketOpportunityRow.tvl_usd == None)  # noqa: E711
        )
    if config.preferred_chains:
        upper_chains = [c.upper() for c in config.preferred_chains]
        q = q.where(MarketOpportunityRow.chain.in_(upper_chains))
    if not config.include_borrow_routes:
        q = q.where(MarketOpportunityRow.side == "SUPPLY")

    rows = (await db.execute(q)).scalars().all()
    return list(rows)


def _build_response(
    entry_asset: str,
    entry_amount_usd: float,
    holding_period_days: int,
    routes: list,
) -> RouteResponse:
    """Build a RouteResponse from a list of YieldRoute objects."""
    from opportunity_schema.schema import OpportunitySide

    route_outs = [_route_to_out(r) for r in routes]

    # Pick the best route that isn't flagged as anomalous
    best_supply = None
    best_borrow = None
    for r in route_outs:
        if r.side == "SUPPLY" and best_supply is None and "ANOMALOUS_APY" not in r.risk_flags:
            best_supply = r
        elif r.side == "BORROW" and best_borrow is None and "ANOMALOUS_APY" not in r.risk_flags:
            best_borrow = r

    return RouteResponse(
        entry_asset=entry_asset,
        entry_amount_usd=entry_amount_usd,
        holding_period_days=holding_period_days,
        total_routes=len(route_outs),
        routes=route_outs,
        best_supply_route=best_supply,
        best_borrow_route=best_borrow,
        computed_at=datetime.now(UTC).isoformat(),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/routes", response_model=RouteResponse)
async def find_routes(
    body: RouteRequest,
    db: AsyncSession = Depends(get_db),
) -> RouteResponse:
    """
    Find all viable yield routes for the given entry asset and amount.

    Returns routes sorted by net APY descending, with conversion costs,
    rate impact, capacity constraints, and risk flags fully evaluated.
    """
    rows = await _load_rows(db, body.config)
    optimizer = _build_optimizer(rows, body.config, body.holding_period_days)

    routes = optimizer.find_routes(
        body.entry_asset,
        body.entry_amount_usd,
    )

    return _build_response(
        body.entry_asset,
        body.entry_amount_usd,
        body.holding_period_days,
        routes,
    )


@router.post("/compare", response_model=CompareResponse)
async def compare_routes(
    body: CompareRequest,
    db: AsyncSession = Depends(get_db),
) -> CompareResponse:
    """
    Compare best yield routes for multiple entry assets side by side.

    Useful for answering: "Where's the better yield — $1M in ETH or $1M in USDC?"
    """
    rows = await _load_rows(db, body.config)
    optimizer = _build_optimizer(rows, body.config, body.holding_period_days)

    comparisons: list[RouteResponse] = []
    for entry in body.routes:
        routes = optimizer.find_routes(entry.entry_asset, entry.entry_amount_usd)
        comparisons.append(_build_response(
            entry.entry_asset,
            entry.entry_amount_usd,
            body.holding_period_days,
            routes,
        ))

    return CompareResponse(
        comparisons=comparisons,
        computed_at=datetime.now(UTC).isoformat(),
    )


@router.get("/quick", response_model=RouteResponse)
async def quick_routes(
    asset: str = Query(..., description="Entry asset (e.g. ETH, USDC)"),
    amount: float = Query(1_000_000, gt=0, description="Amount in USD"),
    db: AsyncSession = Depends(get_db),
) -> RouteResponse:
    """
    Quick route lookup with default config — suitable for embedding in other pages.
    """
    config = OptimizerConfig()
    rows = await _load_rows(db, config)
    optimizer = _build_optimizer(rows, config, holding_period_days=90)

    routes = optimizer.find_routes(asset, amount)
    return _build_response(asset, amount, 90, routes)
