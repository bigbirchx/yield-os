"""
Book optimization engine for an institutional lending / trading desk.

Analyses a CreditDesk book against live market opportunities to generate
actionable suggestions: rate improvements, bilateral pricing checks,
collateral efficiency, maturity actions, conversion opportunities, etc.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import Enum
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.book import (
    BookCollateralAllocationRow,
    BookObservedCollateralRow,
    BookPositionRow,
    BookRow,
)
from app.models.opportunity import MarketOpportunityRow
from portfolio import PositionCategory

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Enums & data classes
# ---------------------------------------------------------------------------


class SuggestionType(str, Enum):
    DEFI_RATE_IMPROVEMENT = "DEFI_RATE_IMPROVEMENT"
    DEFI_NEW_OPPORTUNITY = "DEFI_NEW_OPPORTUNITY"
    DEFI_BORROW_OPTIMIZATION = "DEFI_BORROW_OPTIMIZATION"
    BILATERAL_PRICING_CHECK = "BILATERAL_PRICING_CHECK"
    STAKING_RATE_CHECK = "STAKING_RATE_CHECK"
    CAPACITY_WARNING = "CAPACITY_WARNING"
    COLLATERAL_EFFICIENCY = "COLLATERAL_EFFICIENCY"
    RATE_DEGRADATION = "RATE_DEGRADATION"
    CONVERSION_OPPORTUNITY = "CONVERSION_OPPORTUNITY"
    MATURITY_ACTION = "MATURITY_ACTION"


@dataclass
class BookOptimizationSuggestion:
    suggestion_id: str
    type: SuggestionType
    priority: str  # "high", "medium", "low"
    position: dict  # serialised BookPositionRow
    current_rate_pct: float
    market_rate_pct: float
    suggested_opportunity: dict | None
    suggested_route: dict | None
    rate_improvement_bps: float
    estimated_annual_impact_usd: float
    switching_cost_usd: float
    break_even_days: int
    risk_assessment: str
    action_description: str
    execution_steps: list[str]


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = {
    "min_improvement_bps": 50,
    "holding_period_days": 30,
    "max_ltv_pct": 70.0,
    "include_conversion_routes": True,
    "bilateral_comparison": True,
    "maturity_warning_days": 14,
    "capacity_warning_pct": 85.0,
    "collateral_excess_threshold_pct": 20.0,
    "max_suggestions": 30,
}


# ---------------------------------------------------------------------------
# Helper: build route optimizer from DB rows
# ---------------------------------------------------------------------------

def _build_optimizer_from_rows(rows: list[MarketOpportunityRow], holding_period_days: int):
    """Construct a RouteOptimizer from opportunity DB rows."""
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
            liq_data = row.liquidity or {}
            liquidity = LiquidityInfo(**liq_data) if liq_data else LiquidityInfo()

            rate_model = RateModelInfo(**row.rate_model) if row.rate_model else None

            collateral_options = None
            if row.collateral_options:
                collateral_options = [CollateralAssetInfo(**c) for c in row.collateral_options]

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
            log.debug("skip_opp_row", opp_id=row.opportunity_id, exc_info=True)

    config = RouteOptimizerConfig(
        holding_period_days=holding_period_days,
        max_conversion_steps=3,
        exclude_amm_lp=True,
        exclude_pendle=False,
        risk_tolerance=0.5,
        max_risk_score=0.8,
    )
    return RouteOptimizer(opportunities, config=config), opportunities


# ---------------------------------------------------------------------------
# Helper: serialise position row → dict
# ---------------------------------------------------------------------------

def _pos_dict(row: BookPositionRow) -> dict:
    return {
        "loan_id": row.loan_id,
        "customer_id": row.customer_id,
        "counterparty_name": row.counterparty_name,
        "category": row.category,
        "direction": row.direction,
        "principal_asset": row.principal_asset,
        "principal_qty": row.principal_qty,
        "principal_usd": row.principal_usd,
        "effective_date": row.effective_date.isoformat() if row.effective_date else None,
        "maturity_date": row.maturity_date.isoformat() if row.maturity_date else None,
        "tenor": row.tenor,
        "loan_type": row.loan_type,
        "interest_rate_pct": row.interest_rate_pct,
        "status": row.status,
        "protocol_name": row.protocol_name,
        "protocol_chain": row.protocol_chain,
        "is_collateralized": row.is_collateralized,
        "initial_collateralization_ratio_pct": row.initial_collateralization_ratio_pct,
        "rehypothecation_allowed": row.rehypothecation_allowed,
        "current_market_rate_pct": row.current_market_rate_pct,
        "rate_vs_market_bps": row.rate_vs_market_bps,
    }


def _opp_dict(row: MarketOpportunityRow) -> dict:
    return {
        "opportunity_id": row.opportunity_id,
        "venue": row.venue,
        "chain": row.chain,
        "protocol": row.protocol,
        "protocol_slug": row.protocol_slug,
        "market_name": row.market_name,
        "side": row.side,
        "asset_id": row.asset_id,
        "total_apy_pct": row.total_apy_pct,
        "base_apy_pct": row.base_apy_pct,
        "tvl_usd": row.tvl_usd,
        "total_supplied_usd": row.total_supplied_usd,
        "total_borrowed_usd": row.total_borrowed_usd,
        "capacity_remaining": row.capacity_remaining,
        "is_capacity_capped": row.is_capacity_capped,
        "opportunity_type": row.opportunity_type,
    }


def _route_dict(route) -> dict:
    """Serialise a YieldRoute to a dict."""
    return {
        "opportunity_id": route.opportunity.opportunity_id,
        "protocol": route.opportunity.protocol,
        "chain": route.opportunity.chain,
        "target_asset": route.target_asset,
        "side": route.side.value if hasattr(route.side, "value") else str(route.side),
        "gross_apy_pct": round(route.gross_apy_pct, 4),
        "net_apy_pct": round(route.net_apy_pct, 4),
        "conversion_steps": route.conversion_steps,
        "conversion_cost_bps": round(route.conversion_cost_bps, 2),
        "conversion_gas_usd": round(route.conversion_gas_usd, 2),
        "max_deployable_usd": round(route.max_deployable_usd, 2),
        "risk_flags": route.risk_flags,
        "risk_score": round(route.risk_score, 4),
    }


def _suggestion_dict(s: BookOptimizationSuggestion) -> dict:
    return {
        "suggestion_id": s.suggestion_id,
        "type": s.type.value,
        "priority": s.priority,
        "position": s.position,
        "current_rate_pct": s.current_rate_pct,
        "market_rate_pct": s.market_rate_pct,
        "suggested_opportunity": s.suggested_opportunity,
        "suggested_route": s.suggested_route,
        "rate_improvement_bps": s.rate_improvement_bps,
        "estimated_annual_impact_usd": s.estimated_annual_impact_usd,
        "switching_cost_usd": s.switching_cost_usd,
        "break_even_days": s.break_even_days,
        "risk_assessment": s.risk_assessment,
        "action_description": s.action_description,
        "execution_steps": s.execution_steps,
    }


# ---------------------------------------------------------------------------
# Helper: find best market opportunities for an asset
# ---------------------------------------------------------------------------

def _find_best_supply_opps(
    opp_rows: list[MarketOpportunityRow],
    asset_id: str,
    *,
    exclude_protocol_slug: str | None = None,
    limit: int = 5,
    min_tvl_usd: float = 5_000_000,
) -> list[MarketOpportunityRow]:
    """Find the best supply opportunities for a given asset, sorted by APY desc."""
    matches = []
    for r in opp_rows:
        if r.side != "SUPPLY":
            continue
        if r.asset_id != asset_id:
            continue
        if r.total_apy_pct > 100.0 and not r.is_pendle:
            continue  # anomalous
        if min_tvl_usd and (not r.tvl_usd or r.tvl_usd < min_tvl_usd):
            continue  # too small or unknown TVL for institutional sizing
        if exclude_protocol_slug and r.protocol_slug == exclude_protocol_slug:
            continue
        matches.append(r)
    matches.sort(key=lambda r: r.total_apy_pct, reverse=True)
    return matches[:limit]


def _find_best_borrow_opps(
    opp_rows: list[MarketOpportunityRow],
    asset_id: str,
    *,
    exclude_protocol_slug: str | None = None,
    limit: int = 5,
    min_tvl_usd: float = 5_000_000,
) -> list[MarketOpportunityRow]:
    """Find the cheapest borrow opportunities for a given asset."""
    matches = []
    for r in opp_rows:
        if r.side != "BORROW":
            continue
        if r.asset_id != asset_id:
            continue
        if r.total_apy_pct > 100.0:
            continue
        if min_tvl_usd and (not r.tvl_usd or r.tvl_usd < min_tvl_usd):
            continue  # too small or unknown TVL for institutional sizing
        if exclude_protocol_slug and r.protocol_slug == exclude_protocol_slug:
            continue
        matches.append(r)
    matches.sort(key=lambda r: r.total_apy_pct)  # lowest borrow rate first
    return matches[:limit]


def _find_best_supply_rate(
    opp_rows: list[MarketOpportunityRow],
    asset_id: str,
) -> tuple[float | None, MarketOpportunityRow | None]:
    """Return (best_rate_pct, best_opp_row) for supply side."""
    best = _find_best_supply_opps(opp_rows, asset_id, limit=1)
    if best:
        return best[0].total_apy_pct, best[0]
    return None, None


def _find_asset_supply_range(
    opp_rows: list[MarketOpportunityRow],
    asset_id: str,
    min_tvl_usd: float = 5_000_000,
) -> tuple[float | None, float | None]:
    """Return (min_rate, max_rate) for supply-side APYs for this asset."""
    rates = [
        r.total_apy_pct for r in opp_rows
        if r.side == "SUPPLY" and r.asset_id == asset_id
        and r.total_apy_pct <= 100.0
        and not (min_tvl_usd and (not r.tvl_usd or r.tvl_usd < min_tvl_usd))
    ]
    if not rates:
        return None, None
    return min(rates), max(rates)


def _priority_from_impact(impact_usd: float) -> str:
    if abs(impact_usd) >= 500_000:
        return "high"
    if abs(impact_usd) >= 50_000:
        return "medium"
    return "low"


def _estimate_switching_cost_usd(principal_usd: float, conversion_steps: int) -> float:
    """Rough estimate of gas + slippage for switching."""
    base_gas = 15.0 * max(conversion_steps, 1)  # ~$15 per on-chain tx
    slippage = principal_usd * 0.0005  # ~5bps slippage estimate
    return round(base_gas + slippage, 2)


def _break_even_days(switching_cost_usd: float, annual_impact_usd: float) -> int:
    if annual_impact_usd <= 0:
        return 999
    daily = annual_impact_usd / 365.0
    if daily <= 0:
        return 999
    return max(1, int(switching_cost_usd / daily) + 1)


# ---------------------------------------------------------------------------
# BookOptimizer
# ---------------------------------------------------------------------------


class BookOptimizer:
    """Analyse a portfolio book against live market opportunities."""

    async def analyze_book(
        self,
        db: AsyncSession,
        book_id: str,
        config: dict | None = None,
    ) -> dict:
        """Full book analysis returning categorised suggestions + stats."""
        cfg = {**_DEFAULT_CONFIG, **(config or {})}
        min_bps = cfg["min_improvement_bps"]
        today = date.today()

        # Load book positions
        positions = (await db.execute(
            select(BookPositionRow).where(BookPositionRow.book_id == book_id)
        )).scalars().all()
        if not positions:
            return {"error": "No positions found", "book_id": book_id}

        # Load all market opportunities
        opp_rows = (await db.execute(
            select(MarketOpportunityRow)
            .where(MarketOpportunityRow.tvl_usd >= 100_000)
        )).scalars().all()
        opp_rows = list(opp_rows)

        # Load collateral data
        obs_collateral = (await db.execute(
            select(BookObservedCollateralRow)
            .where(BookObservedCollateralRow.book_id == book_id)
        )).scalars().all()

        alloc_rows = (await db.execute(
            select(BookCollateralAllocationRow)
            .where(BookCollateralAllocationRow.book_id == book_id)
        )).scalars().all()

        # Optionally build route optimizer for conversion routes
        optimizer = None
        if cfg["include_conversion_routes"]:
            try:
                optimizer, _ = _build_optimizer_from_rows(
                    opp_rows, cfg["holding_period_days"],
                )
            except Exception:
                log.warning("book_optimizer_route_init_failed", exc_info=True)

        suggestions: list[BookOptimizationSuggestion] = []

        # ── A. DeFi Rate Comparison ──────────────────────────────────
        for pos in positions:
            if pos.category not in (
                PositionCategory.DEFI_SUPPLY.value,
                PositionCategory.NATIVE_STAKING.value,
            ):
                continue

            asset = pos.principal_asset
            our_rate = pos.interest_rate_pct
            protocol_slug = (pos.protocol_name or "").lower().replace(" ", "-")

            # Find best alternative supply opportunity for same asset
            best_opps = _find_best_supply_opps(
                opp_rows, asset, exclude_protocol_slug=protocol_slug,
            )
            if not best_opps:
                continue

            best_opp = best_opps[0]
            best_rate = best_opp.total_apy_pct
            improvement_bps = round((best_rate - our_rate) * 100, 2)

            if improvement_bps < min_bps:
                continue

            annual_impact = pos.principal_usd * (improvement_bps / 10_000)
            switching_cost = _estimate_switching_cost_usd(pos.principal_usd, 1)
            be_days = _break_even_days(switching_cost, annual_impact)

            # Try to find a conversion route if optimizer available
            route_info = None
            if optimizer:
                try:
                    routes = optimizer.find_routes(
                        asset, pos.principal_usd,
                    )
                    # Find route matching the best opportunity
                    for r in routes:
                        if r.opportunity.opportunity_id == best_opp.opportunity_id:
                            route_info = _route_dict(r)
                            switching_cost = r.conversion_gas_usd + pos.principal_usd * (r.conversion_cost_bps / 10_000)
                            be_days = _break_even_days(switching_cost, annual_impact)
                            break
                except Exception:
                    pass

            suggestions.append(BookOptimizationSuggestion(
                suggestion_id=str(uuid.uuid4())[:8],
                type=SuggestionType.DEFI_RATE_IMPROVEMENT,
                priority=_priority_from_impact(annual_impact),
                position=_pos_dict(pos),
                current_rate_pct=our_rate,
                market_rate_pct=best_rate,
                suggested_opportunity=_opp_dict(best_opp),
                suggested_route=route_info,
                rate_improvement_bps=improvement_bps,
                estimated_annual_impact_usd=round(annual_impact, 2),
                switching_cost_usd=round(switching_cost, 2),
                break_even_days=be_days,
                risk_assessment=self._compare_risk(pos, best_opp),
                action_description=(
                    f"{pos.protocol_name or 'Current'} {asset} Supply: earning {our_rate:.2f}% "
                    f"but {best_opp.protocol} on {best_opp.chain} is paying {best_rate:.2f}%. "
                    f"On ${pos.principal_usd:,.0f}, improvement is ${annual_impact:,.0f}/year."
                ),
                execution_steps=self._supply_switch_steps(pos, best_opp),
            ))

        # ── B. DeFi Borrow Optimization ──────────────────────────────
        for pos in positions:
            if pos.category != PositionCategory.DEFI_BORROW.value:
                continue

            asset = pos.principal_asset
            our_rate = pos.interest_rate_pct
            protocol_slug = (pos.protocol_name or "").lower().replace(" ", "-")

            best_opps = _find_best_borrow_opps(
                opp_rows, asset, exclude_protocol_slug=protocol_slug,
            )
            if not best_opps:
                continue

            best_opp = best_opps[0]
            best_rate = best_opp.total_apy_pct
            # For borrows, improvement = our_rate - best_rate (lower is better)
            saving_bps = round((our_rate - best_rate) * 100, 2)

            if saving_bps < min_bps:
                continue

            annual_saving = pos.principal_usd * (saving_bps / 10_000)
            switching_cost = _estimate_switching_cost_usd(pos.principal_usd, 2)
            be_days = _break_even_days(switching_cost, annual_saving)

            suggestions.append(BookOptimizationSuggestion(
                suggestion_id=str(uuid.uuid4())[:8],
                type=SuggestionType.DEFI_BORROW_OPTIMIZATION,
                priority=_priority_from_impact(annual_saving),
                position=_pos_dict(pos),
                current_rate_pct=our_rate,
                market_rate_pct=best_rate,
                suggested_opportunity=_opp_dict(best_opp),
                suggested_route=None,
                rate_improvement_bps=saving_bps,
                estimated_annual_impact_usd=round(annual_saving, 2),
                switching_cost_usd=round(switching_cost, 2),
                break_even_days=be_days,
                risk_assessment=self._compare_risk(pos, best_opp),
                action_description=(
                    f"{pos.protocol_name or 'Current'} {asset} Borrow at {our_rate:.2f}%: "
                    f"{best_opp.protocol} has {asset} at {best_rate:.2f}%. "
                    f"On ${pos.principal_usd:,.0f}, saving {saving_bps:.0f}bps = ${annual_saving:,.0f}/year."
                ),
                execution_steps=[
                    f"Repay {asset} borrow on {pos.protocol_name}",
                    f"Withdraw collateral from {pos.protocol_name}",
                    f"Deposit collateral into {best_opp.protocol} on {best_opp.chain}",
                    f"Borrow {asset} on {best_opp.protocol} at {best_rate:.2f}%",
                    "Update internal tracking and risk monitoring",
                ],
            ))

        # ── C. Bilateral Pricing Intelligence ────────────────────────
        if cfg["bilateral_comparison"]:
            for pos in positions:
                if pos.category != PositionCategory.BILATERAL_LOAN_OUT.value:
                    continue

                asset = pos.principal_asset
                our_rate = pos.interest_rate_pct

                best_rate, best_opp = _find_best_supply_rate(opp_rows, asset)
                if best_rate is None:
                    continue

                rate_min, rate_max = _find_asset_supply_range(opp_rows, asset)
                delta_bps = round((our_rate - best_rate) * 100, 2)

                # Underpriced: our lending rate < best DeFi supply rate
                if delta_bps < -min_bps:
                    annual_impact = pos.principal_usd * (abs(delta_bps) / 10_000)
                    suggestions.append(BookOptimizationSuggestion(
                        suggestion_id=str(uuid.uuid4())[:8],
                        type=SuggestionType.BILATERAL_PRICING_CHECK,
                        priority=_priority_from_impact(annual_impact),
                        position=_pos_dict(pos),
                        current_rate_pct=our_rate,
                        market_rate_pct=best_rate,
                        suggested_opportunity=_opp_dict(best_opp) if best_opp else None,
                        suggested_route=None,
                        rate_improvement_bps=abs(delta_bps),
                        estimated_annual_impact_usd=round(annual_impact, 2),
                        switching_cost_usd=0.0,
                        break_even_days=0,
                        risk_assessment=(
                            f"Bilateral loan carries counterparty credit risk not present in DeFi. "
                            f"However, at {our_rate:.2f}% vs DeFi range {rate_min:.2f}-{rate_max:.2f}%, "
                            f"the bilateral premium is negative."
                        ),
                        action_description=(
                            f"Loan to {pos.counterparty_name} at {our_rate:.2f}% ({asset}): "
                            f"DeFi supply rates currently range {rate_min:.2f}-{rate_max:.2f}%. "
                            f"Lending {abs(delta_bps):.0f}bps below best DeFi rate — "
                            f"consider repricing or redeploying ${pos.principal_usd:,.0f} to DeFi."
                        ),
                        execution_steps=[
                            "Review bilateral agreement terms and recall provisions",
                            f"If rate renegotiation possible, target at least {best_rate:.2f}%",
                            f"Alternative: recall loan and deploy to {best_opp.protocol if best_opp else 'DeFi'} "
                            f"at {best_rate:.2f}%",
                            "Factor in bilateral premium for credit risk if counterparty is strong",
                        ],
                    ))

        # ── D. Staking Rate Verification ─────────────────────────────
        for pos in positions:
            if pos.category != PositionCategory.NATIVE_STAKING.value:
                continue

            asset = pos.principal_asset
            our_rate = pos.interest_rate_pct

            # Look up current staking / liquid staking rates
            staking_opps = [
                r for r in opp_rows
                if r.asset_id == asset
                and r.side == "SUPPLY"
                and r.opportunity_type in ("STAKING", "RESTAKING")
                and r.total_apy_pct <= 100.0
                and r.tvl_usd and r.tvl_usd >= 5_000_000
            ]
            if not staking_opps:
                continue

            best_staking = max(staking_opps, key=lambda r: r.total_apy_pct)
            market_rate = best_staking.total_apy_pct
            delta_bps = round(abs(our_rate - market_rate) * 100, 2)

            if delta_bps < min_bps:
                continue

            annual_impact = pos.principal_usd * (delta_bps / 10_000)
            direction = "below" if our_rate < market_rate else "above"

            suggestions.append(BookOptimizationSuggestion(
                suggestion_id=str(uuid.uuid4())[:8],
                type=SuggestionType.STAKING_RATE_CHECK,
                priority=_priority_from_impact(annual_impact),
                position=_pos_dict(pos),
                current_rate_pct=our_rate,
                market_rate_pct=market_rate,
                suggested_opportunity=_opp_dict(best_staking),
                suggested_route=None,
                rate_improvement_bps=delta_bps,
                estimated_annual_impact_usd=round(annual_impact, 2),
                switching_cost_usd=0.0,
                break_even_days=0,
                risk_assessment=(
                    f"Staking rate {direction} current market by {delta_bps:.0f}bps. "
                    f"Verify rate is still accurate or consider switching validators/providers."
                ),
                action_description=(
                    f"{pos.counterparty_name} {asset} staking at {our_rate:.2f}%: "
                    f"current market staking rate is {market_rate:.2f}% ({direction} by {delta_bps:.0f}bps). "
                    f"Impact on ${pos.principal_usd:,.0f}: ${annual_impact:,.0f}/year."
                ),
                execution_steps=[
                    f"Verify booked rate {our_rate:.2f}% reflects current validator performance",
                    f"Compare with {best_staking.protocol} at {market_rate:.2f}%",
                    "If rate is stale, update booking or switch staking provider",
                ],
            ))

        # ── E. Collateral Efficiency ─────────────────────────────────
        collateral_by_cust: dict[int, list] = defaultdict(list)
        for c in obs_collateral:
            collateral_by_cust[c.customer_id].append(c)

        positions_by_cust: dict[int, list] = defaultdict(list)
        for p in positions:
            positions_by_cust[p.customer_id].append(p)

        alloc_by_loan: dict[int, list] = defaultdict(list)
        for a in alloc_rows:
            alloc_by_loan[a.loan_id].append(a)

        for cust_id, cust_collateral in collateral_by_cust.items():
            cust_positions = positions_by_cust.get(cust_id, [])
            if not cust_positions:
                continue

            # Total collateral USD (positive = pledged to us)
            total_col_usd = sum(
                a.allocated_usd
                for p in cust_positions
                for a in alloc_by_loan.get(p.loan_id, [])
            )

            # Total required collateral
            total_req_usd = sum(
                p.principal_usd * ((p.initial_collateralization_ratio_pct or 0) / 100.0)
                for p in cust_positions
                if p.is_collateralized and p.direction == "Loan_Out"
            )

            if total_req_usd <= 0:
                continue

            excess_usd = total_col_usd - total_req_usd
            excess_pct = (excess_usd / total_req_usd * 100) if total_req_usd > 0 else 0

            if excess_pct < cfg["collateral_excess_threshold_pct"]:
                continue

            # Check rehypothecation potential
            any_rehyp = any(p.rehypothecation_allowed for p in cust_positions)

            # Estimate potential yield on excess
            # Use conservative supply rate for the main collateral asset
            col_assets = set(c.collateral_asset for c in cust_collateral if c.units_posted > 0)
            potential_yield_usd = 0.0
            for col_asset in col_assets:
                best_rate, _ = _find_best_supply_rate(opp_rows, col_asset)
                if best_rate:
                    potential_yield_usd += excess_usd * (best_rate / 100.0) / len(col_assets)

            counterparty_name = cust_positions[0].counterparty_name

            suggestions.append(BookOptimizationSuggestion(
                suggestion_id=str(uuid.uuid4())[:8],
                type=SuggestionType.COLLATERAL_EFFICIENCY,
                priority=_priority_from_impact(potential_yield_usd),
                position=_pos_dict(cust_positions[0]),
                current_rate_pct=0.0,
                market_rate_pct=0.0,
                suggested_opportunity=None,
                suggested_route=None,
                rate_improvement_bps=0.0,
                estimated_annual_impact_usd=round(potential_yield_usd, 2),
                switching_cost_usd=0.0,
                break_even_days=0,
                risk_assessment=(
                    f"Over-collateralised by {excess_pct:.1f}% (${excess_usd:,.0f} excess). "
                    + ("Rehypothecation IS allowed — excess could earn yield." if any_rehyp
                       else "Rehypothecation NOT allowed — excess is idle.")
                ),
                action_description=(
                    f"{counterparty_name}: ${total_col_usd:,.0f} collateral posted vs "
                    f"${total_req_usd:,.0f} required. Excess: ${excess_usd:,.0f} ({excess_pct:.1f}%). "
                    + (f"With rehypothecation, could earn ~${potential_yield_usd:,.0f}/year." if any_rehyp
                       else "Consider requesting collateral return or renegotiating terms.")
                ),
                execution_steps=(
                    [
                        f"Verify collateral requirement: ${total_req_usd:,.0f}",
                        f"Excess collateral: ${excess_usd:,.0f}",
                        "Deploy excess to DeFi supply (rehypothecation allowed)",
                        "Set up monitoring for margin calls requiring collateral return",
                    ] if any_rehyp else [
                        f"Verify collateral requirement: ${total_req_usd:,.0f}",
                        f"Excess collateral: ${excess_usd:,.0f}",
                        "Request partial collateral return from counterparty",
                        "Or negotiate rehypothecation rights in next contract renewal",
                    ]
                ),
            ))

        # ── F. Maturity Actions ──────────────────────────────────────
        warning_days = cfg["maturity_warning_days"]
        for pos in positions:
            if pos.tenor != "Fixed" or pos.maturity_date is None:
                continue

            mat = pos.maturity_date
            if isinstance(mat, datetime):
                mat = mat.date()
            days_to_mat = (mat - today).days

            if days_to_mat < 0 or days_to_mat > warning_days:
                continue

            asset = pos.principal_asset
            our_rate = pos.interest_rate_pct
            best_rate, best_opp = _find_best_supply_rate(opp_rows, asset)

            suggestions.append(BookOptimizationSuggestion(
                suggestion_id=str(uuid.uuid4())[:8],
                type=SuggestionType.MATURITY_ACTION,
                priority="high" if days_to_mat <= 3 else "medium",
                position=_pos_dict(pos),
                current_rate_pct=our_rate,
                market_rate_pct=best_rate or 0.0,
                suggested_opportunity=_opp_dict(best_opp) if best_opp else None,
                suggested_route=None,
                rate_improvement_bps=round((best_rate - our_rate) * 100, 2) if best_rate else 0.0,
                estimated_annual_impact_usd=0.0,
                switching_cost_usd=0.0,
                break_even_days=0,
                risk_assessment=f"Position matures in {days_to_mat} day(s). Requires rollover decision.",
                action_description=(
                    f"{pos.counterparty_name} {asset} ({pos.direction}): matures {mat.isoformat()} "
                    f"({days_to_mat}d). Booked at {our_rate:.2f}%, "
                    + (f"current market: {best_rate:.2f}%." if best_rate else "no market comparison available.")
                ),
                execution_steps=[
                    f"Position matures {mat.isoformat()} ({days_to_mat} days)",
                    f"Current rate: {our_rate:.2f}%"
                    + (f", market rate: {best_rate:.2f}%" if best_rate else ""),
                    "Decide: rollover at current rate, renegotiate, or redeploy",
                    "If bilateral: contact counterparty for rollover terms",
                ],
            ))

        # ── G. Capacity Warnings ─────────────────────────────────────
        cap_threshold = cfg["capacity_warning_pct"]
        for pos in positions:
            if pos.category not in (
                PositionCategory.DEFI_SUPPLY.value,
                PositionCategory.DEFI_BORROW.value,
            ):
                continue

            # Find matched opportunity
            protocol_slug = (pos.protocol_name or "").lower().replace(" ", "-")
            matched_opp = None
            for r in opp_rows:
                if (r.asset_id == pos.principal_asset
                        and protocol_slug in r.protocol_slug.lower()
                        and ((pos.category == PositionCategory.DEFI_SUPPLY.value and r.side == "SUPPLY")
                             or (pos.category == PositionCategory.DEFI_BORROW.value and r.side == "BORROW"))):
                    matched_opp = r
                    break

            if not matched_opp:
                continue

            # Check utilization
            liq_data = matched_opp.liquidity or {}
            util = liq_data.get("utilization_rate_pct")
            if util is not None and util >= cap_threshold:
                suggestions.append(BookOptimizationSuggestion(
                    suggestion_id=str(uuid.uuid4())[:8],
                    type=SuggestionType.CAPACITY_WARNING,
                    priority="high" if util >= 95 else "medium",
                    position=_pos_dict(pos),
                    current_rate_pct=pos.interest_rate_pct,
                    market_rate_pct=matched_opp.total_apy_pct,
                    suggested_opportunity=_opp_dict(matched_opp),
                    suggested_route=None,
                    rate_improvement_bps=0.0,
                    estimated_annual_impact_usd=0.0,
                    switching_cost_usd=0.0,
                    break_even_days=0,
                    risk_assessment=(
                        f"Pool utilization at {util:.1f}%. "
                        f"{'Rate may spike past kink. ' if util > 90 else ''}"
                        f"Withdrawal liquidity may be constrained."
                    ),
                    action_description=(
                        f"{pos.protocol_name} {pos.principal_asset}: pool utilization at {util:.1f}% "
                        f"(threshold: {cap_threshold:.0f}%). "
                        f"Position: ${pos.principal_usd:,.0f}."
                    ),
                    execution_steps=[
                        f"Monitor {pos.protocol_name} utilization (currently {util:.1f}%)",
                        "Prepare contingency exit plan if utilization exceeds 95%",
                        "Consider partial withdrawal to reduce concentration",
                    ],
                ))

        # ── H. Conversion Opportunities ──────────────────────────────
        if cfg["include_conversion_routes"] and optimizer:
            for pos in positions:
                if pos.category != PositionCategory.DEFI_SUPPLY.value:
                    continue

                asset = pos.principal_asset
                our_rate = pos.interest_rate_pct

                try:
                    routes = optimizer.find_routes(asset, pos.principal_usd)
                except Exception:
                    continue

                # Look for conversion routes (different target asset) that beat current rate
                for route in routes:
                    if route.target_asset == asset:
                        continue  # same asset, already covered in section A
                    if "ANOMALOUS_APY" in route.risk_flags:
                        continue

                    improvement_bps = round((route.net_apy_pct - our_rate) * 100, 2)
                    if improvement_bps < min_bps:
                        continue

                    annual_impact = pos.principal_usd * (improvement_bps / 10_000)
                    switching_cost = route.conversion_gas_usd + pos.principal_usd * (route.conversion_cost_bps / 10_000)
                    be_days = _break_even_days(switching_cost, annual_impact)

                    suggestions.append(BookOptimizationSuggestion(
                        suggestion_id=str(uuid.uuid4())[:8],
                        type=SuggestionType.CONVERSION_OPPORTUNITY,
                        priority=_priority_from_impact(annual_impact),
                        position=_pos_dict(pos),
                        current_rate_pct=our_rate,
                        market_rate_pct=route.net_apy_pct,
                        suggested_opportunity=None,
                        suggested_route=_route_dict(route),
                        rate_improvement_bps=improvement_bps,
                        estimated_annual_impact_usd=round(annual_impact, 2),
                        switching_cost_usd=round(switching_cost, 2),
                        break_even_days=be_days,
                        risk_assessment=(
                            f"Requires converting {asset} → {route.target_asset} "
                            f"({route.conversion_steps} step(s), {route.conversion_cost_bps:.1f}bps). "
                            f"Risk score: {route.risk_score:.2f}."
                        ),
                        action_description=(
                            f"Convert {asset} → {route.target_asset} and deploy to "
                            f"{route.opportunity.protocol} on {route.opportunity.chain}: "
                            f"net {route.net_apy_pct:.2f}% vs current {our_rate:.2f}%. "
                            f"On ${pos.principal_usd:,.0f}: ${annual_impact:,.0f}/year improvement. "
                            f"Break-even: {be_days}d."
                        ),
                        execution_steps=[
                            f"Withdraw {asset} from {pos.protocol_name}",
                            f"Convert {asset} → {route.target_asset} "
                            f"({route.conversion_steps} step(s), ~${switching_cost:,.0f} cost)",
                            f"Deploy {route.target_asset} to {route.opportunity.protocol} "
                            f"on {route.opportunity.chain}",
                            "Update internal position tracking",
                        ],
                    ))
                    break  # only best conversion route per position

        # ── Sort by impact and limit ─────────────────────────────────
        suggestions.sort(key=lambda s: s.estimated_annual_impact_usd, reverse=True)
        suggestions = suggestions[: cfg["max_suggestions"]]

        # ── Summary stats ────────────────────────────────────────────
        total_impact = sum(s.estimated_annual_impact_usd for s in suggestions)
        by_type: dict[str, int] = defaultdict(int)
        by_priority: dict[str, int] = defaultdict(int)
        for s in suggestions:
            by_type[s.type.value] += 1
            by_priority[s.priority] += 1

        return {
            "book_id": book_id,
            "analyzed_at": datetime.now(UTC).isoformat(),
            "total_positions_analyzed": len(positions),
            "total_opportunities_scanned": len(opp_rows),
            "total_suggestions": len(suggestions),
            "total_estimated_annual_impact_usd": round(total_impact, 2),
            "suggestions_by_type": dict(by_type),
            "suggestions_by_priority": dict(by_priority),
            "suggestions": [_suggestion_dict(s) for s in suggestions],
        }

    # ── Simple report endpoints ──────────────────────────────────────

    async def defi_vs_market_comparison(
        self, db: AsyncSession, book_id: str,
    ) -> list[dict]:
        """For each DeFi position, show our rate vs current market rate."""
        positions = (await db.execute(
            select(BookPositionRow)
            .where(BookPositionRow.book_id == book_id)
            .where(BookPositionRow.category.in_([
                PositionCategory.DEFI_SUPPLY.value,
                PositionCategory.DEFI_BORROW.value,
                PositionCategory.NATIVE_STAKING.value,
            ]))
            .order_by(BookPositionRow.principal_usd.desc())
        )).scalars().all()

        opp_rows = list((await db.execute(
            select(MarketOpportunityRow)
        )).scalars().all())

        results = []
        for pos in positions:
            asset = pos.principal_asset
            our_rate = pos.interest_rate_pct
            side = "SUPPLY" if pos.category != PositionCategory.DEFI_BORROW.value else "BORROW"

            # Find current market rate
            market_rate = pos.current_market_rate_pct
            best_rate = None
            best_protocol = None

            if side == "SUPPLY":
                best_opps = _find_best_supply_opps(opp_rows, asset, limit=1)
            else:
                best_opps = _find_best_borrow_opps(opp_rows, asset, limit=1)

            if best_opps:
                best_rate = best_opps[0].total_apy_pct
                best_protocol = best_opps[0].protocol

            delta_bps = round((our_rate - (market_rate or 0)) * 100, 2) if market_rate else None
            best_delta_bps = round((our_rate - best_rate) * 100, 2) if best_rate else None

            results.append({
                "loan_id": pos.loan_id,
                "protocol_name": pos.protocol_name,
                "protocol_chain": pos.protocol_chain,
                "asset": asset,
                "direction": pos.direction,
                "category": pos.category,
                "principal_usd": pos.principal_usd,
                "our_rate_pct": our_rate,
                "matched_market_rate_pct": market_rate,
                "delta_vs_matched_bps": delta_bps,
                "best_market_rate_pct": best_rate,
                "best_market_protocol": best_protocol,
                "delta_vs_best_bps": best_delta_bps,
            })

        # Sort by delta (biggest underperformers first)
        results.sort(key=lambda r: r.get("delta_vs_best_bps") or 0)
        return results

    async def bilateral_pricing_report(
        self, db: AsyncSession, book_id: str,
    ) -> list[dict]:
        """For each bilateral loan, compare rate to best DeFi rate."""
        positions = (await db.execute(
            select(BookPositionRow)
            .where(BookPositionRow.book_id == book_id)
            .where(BookPositionRow.category.in_([
                PositionCategory.BILATERAL_LOAN_OUT.value,
                PositionCategory.BILATERAL_BORROW_IN.value,
            ]))
            .order_by(BookPositionRow.principal_usd.desc())
        )).scalars().all()

        opp_rows = list((await db.execute(
            select(MarketOpportunityRow)
        )).scalars().all())

        results = []
        for pos in positions:
            asset = pos.principal_asset
            our_rate = pos.interest_rate_pct

            if pos.direction == "Loan_Out":
                best_rate, best_opp = _find_best_supply_rate(opp_rows, asset)
                rate_min, rate_max = _find_asset_supply_range(opp_rows, asset)
            else:
                best_opps = _find_best_borrow_opps(opp_rows, asset, limit=1)
                best_opp = best_opps[0] if best_opps else None
                best_rate = best_opp.total_apy_pct if best_opp else None
                rate_min, rate_max = None, None

            premium_bps = round((our_rate - (best_rate or 0)) * 100, 2) if best_rate else None

            assessment = "no_defi_data"
            if premium_bps is not None:
                if pos.direction == "Loan_Out":
                    if premium_bps >= 200:
                        assessment = "well_priced"
                    elif premium_bps >= 0:
                        assessment = "thin_premium"
                    else:
                        assessment = "underpriced"
                else:
                    # Borrow: we want to pay less than market
                    if premium_bps <= -50:
                        assessment = "well_priced"
                    elif premium_bps <= 50:
                        assessment = "market_rate"
                    else:
                        assessment = "overpriced"

            results.append({
                "loan_id": pos.loan_id,
                "counterparty_name": pos.counterparty_name,
                "customer_id": pos.customer_id,
                "direction": pos.direction,
                "asset": asset,
                "principal_usd": pos.principal_usd,
                "our_rate_pct": our_rate,
                "best_defi_rate_pct": best_rate,
                "best_defi_protocol": best_opp.protocol if best_opp else None,
                "defi_rate_range_min": rate_min,
                "defi_rate_range_max": rate_max,
                "premium_discount_bps": premium_bps,
                "assessment": assessment,
                "is_collateralized": pos.is_collateralized,
                "tenor": pos.tenor,
            })

        return results

    async def collateral_efficiency_report(
        self, db: AsyncSession, book_id: str,
    ) -> list[dict]:
        """Per-counterparty collateral analysis."""
        positions = (await db.execute(
            select(BookPositionRow).where(BookPositionRow.book_id == book_id)
        )).scalars().all()

        obs_collateral = (await db.execute(
            select(BookObservedCollateralRow)
            .where(BookObservedCollateralRow.book_id == book_id)
        )).scalars().all()

        alloc_rows = (await db.execute(
            select(BookCollateralAllocationRow)
            .where(BookCollateralAllocationRow.book_id == book_id)
        )).scalars().all()

        opp_rows = list((await db.execute(
            select(MarketOpportunityRow).where(MarketOpportunityRow.side == "SUPPLY")
        )).scalars().all())

        # Group by customer
        positions_by_cust: dict[int, list] = defaultdict(list)
        for p in positions:
            positions_by_cust[p.customer_id].append(p)

        col_by_cust: dict[int, list] = defaultdict(list)
        for c in obs_collateral:
            col_by_cust[c.customer_id].append(c)

        alloc_by_loan: dict[int, list] = defaultdict(list)
        for a in alloc_rows:
            alloc_by_loan[a.loan_id].append(a)

        results = []
        for cust_id, cust_positions in positions_by_cust.items():
            cust_col = col_by_cust.get(cust_id, [])

            counterparty_name = cust_positions[0].counterparty_name

            # Collateral allocated
            total_alloc_usd = sum(
                a.allocated_usd
                for p in cust_positions
                for a in alloc_by_loan.get(p.loan_id, [])
            )

            # Required collateral
            total_req_usd = sum(
                p.principal_usd * ((p.initial_collateralization_ratio_pct or 0) / 100.0)
                for p in cust_positions
                if p.is_collateralized and p.direction == "Loan_Out"
            )

            total_loans_usd = sum(
                p.principal_usd for p in cust_positions if p.direction == "Loan_Out"
            )

            if total_req_usd <= 0 and total_alloc_usd <= 0:
                continue

            excess_usd = total_alloc_usd - total_req_usd
            excess_pct = (excess_usd / total_req_usd * 100) if total_req_usd > 0 else 0

            any_rehyp = any(p.rehypothecation_allowed for p in cust_positions)

            # Potential yield on excess
            col_assets = set(c.collateral_asset for c in cust_col if c.units_posted > 0)
            potential_yield_usd = 0.0
            potential_yield_details = []
            for col_asset in col_assets:
                best_rate, best_opp = _find_best_supply_rate(opp_rows, col_asset)
                if best_rate and excess_usd > 0:
                    asset_yield = excess_usd * (best_rate / 100.0) / max(len(col_assets), 1)
                    potential_yield_usd += asset_yield
                    potential_yield_details.append({
                        "asset": col_asset,
                        "best_rate_pct": best_rate,
                        "protocol": best_opp.protocol if best_opp else None,
                        "estimated_yield_usd": round(asset_yield, 2),
                    })

            results.append({
                "customer_id": cust_id,
                "counterparty_name": counterparty_name,
                "total_loans_usd": round(total_loans_usd, 2),
                "total_collateral_usd": round(total_alloc_usd, 2),
                "total_required_usd": round(total_req_usd, 2),
                "excess_usd": round(excess_usd, 2),
                "excess_pct": round(excess_pct, 2),
                "rehypothecation_allowed": any_rehyp,
                "collateral_assets": list(col_assets),
                "potential_yield_usd": round(potential_yield_usd, 2),
                "potential_yield_details": potential_yield_details,
                "status": (
                    "over_collateralised" if excess_pct > 20
                    else "adequately_collateralised" if excess_pct >= -5
                    else "under_collateralised"
                ),
            })

        results.sort(key=lambda r: r["excess_usd"], reverse=True)
        return results

    async def maturity_calendar(
        self, db: AsyncSession, book_id: str,
    ) -> list[dict]:
        """Upcoming maturities sorted by date."""
        today = date.today()

        positions = (await db.execute(
            select(BookPositionRow)
            .where(BookPositionRow.book_id == book_id)
            .where(BookPositionRow.tenor == "Fixed")
            .where(BookPositionRow.maturity_date != None)  # noqa: E711
        )).scalars().all()

        opp_rows = list((await db.execute(
            select(MarketOpportunityRow)
        )).scalars().all())

        results = []
        for pos in positions:
            mat = pos.maturity_date
            if isinstance(mat, datetime):
                mat = mat.date()
            days_to_mat = (mat - today).days

            asset = pos.principal_asset
            best_rate, best_opp = _find_best_supply_rate(opp_rows, asset)

            results.append({
                "loan_id": pos.loan_id,
                "counterparty_name": pos.counterparty_name,
                "customer_id": pos.customer_id,
                "direction": pos.direction,
                "category": pos.category,
                "asset": asset,
                "principal_usd": pos.principal_usd,
                "interest_rate_pct": pos.interest_rate_pct,
                "maturity_date": mat.isoformat(),
                "days_to_maturity": days_to_mat,
                "status": (
                    "expired" if days_to_mat < 0
                    else "imminent" if days_to_mat <= 3
                    else "upcoming" if days_to_mat <= 14
                    else "scheduled"
                ),
                "current_market_rate_pct": best_rate,
                "market_protocol": best_opp.protocol if best_opp else None,
                "rate_delta_bps": round((pos.interest_rate_pct - best_rate) * 100, 2) if best_rate else None,
            })

        results.sort(key=lambda r: r["days_to_maturity"])
        return results

    # ── Private helpers ──────────────────────────────────────────────

    @staticmethod
    def _compare_risk(pos: BookPositionRow, opp: MarketOpportunityRow) -> str:
        """Generate a human-readable risk comparison."""
        parts = []

        if opp.tvl_usd and opp.tvl_usd < 10_000_000:
            parts.append(f"Target pool TVL is ${opp.tvl_usd / 1e6:.1f}M (small)")
        elif opp.tvl_usd and opp.tvl_usd > 100_000_000:
            parts.append(f"Target pool TVL is ${opp.tvl_usd / 1e6:.0f}M (deep liquidity)")

        liq = opp.liquidity or {}
        util = liq.get("utilization_rate_pct")
        if util and util > 80:
            parts.append(f"High utilization ({util:.0f}%) may limit withdrawal speed")

        if opp.chain != (pos.protocol_chain or ""):
            parts.append(f"Cross-chain move ({pos.protocol_chain} → {opp.chain})")

        if not parts:
            parts.append("Comparable risk profile to current position")

        return ". ".join(parts) + "."

    @staticmethod
    def _supply_switch_steps(pos: BookPositionRow, opp: MarketOpportunityRow) -> list[str]:
        """Generate execution steps for switching a supply position."""
        steps = [
            f"Withdraw {pos.principal_asset} from {pos.protocol_name or 'current protocol'}",
        ]
        if opp.chain != (pos.protocol_chain or ""):
            steps.append(f"Bridge {pos.principal_asset} from {pos.protocol_chain} to {opp.chain}")
        if opp.asset_id != pos.principal_asset:
            steps.append(f"Convert {pos.principal_asset} → {opp.asset_id}")
        steps.extend([
            f"Deposit into {opp.protocol} on {opp.chain} ({opp.market_name or opp.market_id})",
            "Update internal position tracking and risk monitoring",
        ])
        return steps
