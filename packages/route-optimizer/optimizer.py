"""
Yield route optimizer for Yield OS.

Answers the question: "I have $X of asset A — what's the highest net yield
considering conversion costs, collateral requirements, capacity constraints,
and rate impact?"

Integrates with :class:`~asset_registry.conversions.ConversionRouter` for
multi-hop conversion costing and :class:`~opportunity_schema.schema.MarketOpportunity`
as the canonical opportunity representation.

Usage::

    from route_optimizer import RouteOptimizer, RouteOptimizerConfig

    optimizer = RouteOptimizer(opportunities, config=RouteOptimizerConfig())
    routes = optimizer.find_routes("USDC", amount_usd=5_000_000)
    best = optimizer.find_best_route("USDC", amount_usd=5_000_000)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from asset_registry.conversions import ConversionEdge, ConversionRouter
from asset_registry.taxonomy import (
    ASSET_REGISTRY,
    AssetUmbrella,
    FungibilityTier,
    get_fungible_group,
    get_umbrella_assets,
)
from opportunity_schema.schema import (
    CollateralAssetInfo,
    LiquidityInfo,
    MarketOpportunity,
    OpportunitySide,
    RateModelInfo,
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CollateralRequirement:
    """What collateral is needed to execute a borrow-side route."""

    collateral_asset: str
    collateral_amount_usd: float
    max_ltv_pct: float
    liquidation_ltv_pct: float
    liquidation_buffer_pct: float
    conversion_cost_bps: float
    opportunity_cost_apy_pct: float


@dataclass(frozen=True)
class YieldRoute:
    """A fully evaluated path from holding asset to yield opportunity."""

    # ── Identity ────────────────────────────────────────────────────────
    opportunity: MarketOpportunity
    source_asset: str
    target_asset: str
    side: OpportunitySide

    # ── Conversion ──────────────────────────────────────────────────────
    conversion_path: list[ConversionEdge]
    conversion_steps: int
    conversion_cost_bps: float
    conversion_gas_usd: float
    conversion_time_min_seconds: int
    conversion_time_max_seconds: int
    is_conversion_deterministic: bool

    # ── Yield ───────────────────────────────────────────────────────────
    gross_apy_pct: float
    net_apy_pct: float
    annualized_conversion_cost_pct: float

    # ── Capacity ────────────────────────────────────────────────────────
    max_deployable_usd: float
    capacity_limited: bool

    # ── Rate impact ─────────────────────────────────────────────────────
    rate_impact_bps: float
    post_deposit_apy_pct: float | None

    # ── Risk ────────────────────────────────────────────────────────────
    risk_flags: list[str]
    risk_score: float

    # ── Collateral (borrow routes only) ─────────────────────────────────
    collateral: CollateralRequirement | None = None

    # ── Metadata ────────────────────────────────────────────────────────
    computed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RouteOptimizerConfig:
    """Tunable knobs for the route optimizer."""

    # Holding period — used to annualise one-time conversion costs
    holding_period_days: int = 90

    # Conversion limits
    max_conversion_steps: int = 3
    max_conversion_cost_bps: float = 100.0

    # Opportunity filters
    min_tvl_usd: float = 100_000.0
    min_apy_pct: float = 0.0
    exclude_amm_lp: bool = True
    exclude_pendle: bool = False

    # Capacity
    max_pool_share_pct: float = 10.0
    max_oi_share_pct: float = 5.0

    # Risk
    risk_tolerance: float = 0.5  # 0 = conservative, 1 = aggressive
    max_risk_score: float = 0.8

    # Rate impact
    rate_impact_enabled: bool = True


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DAYS_PER_YEAR = 365.0

# Risk weights for computing composite score
_RISK_WEIGHTS: dict[str, float] = {
    "LOW_TVL": 0.10,
    "HIGH_UTILIZATION": 0.12,
    "CAPACITY_CAPPED": 0.05,
    "WITHDRAWAL_QUEUE": 0.08,
    "LOCKUP": 0.10,
    "NON_DETERMINISTIC_CONVERSION": 0.06,
    "HIGH_CONVERSION_COST": 0.08,
    "RATE_MODEL_KINK": 0.15,
    "LOW_LIQUIDITY": 0.12,
    "STALE_DATA": 0.08,
    "ISOLATED_COLLATERAL": 0.06,
    "ANOMALOUS_APY": 0.25,
}


# ---------------------------------------------------------------------------
# Route optimizer
# ---------------------------------------------------------------------------


class RouteOptimizer:
    """Find and rank yield routes across all known opportunities.

    Parameters
    ----------
    opportunities
        Current snapshot of all market opportunities.
    config
        Tuning parameters.  Defaults are sensible for a mid-size desk.
    router
        Conversion router instance.  Uses the default static graph if not
        provided.
    """

    def __init__(
        self,
        opportunities: list[MarketOpportunity],
        config: RouteOptimizerConfig | None = None,
        router: ConversionRouter | None = None,
    ) -> None:
        self._opportunities = opportunities
        self._config = config or RouteOptimizerConfig()
        self._router = router or ConversionRouter()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def find_routes(
        self,
        source_asset: str,
        amount_usd: float,
        *,
        side: OpportunitySide | None = None,
        umbrella: AssetUmbrella | None = None,
    ) -> list[YieldRoute]:
        """Find all viable yield routes for *source_asset* at *amount_usd*.

        Returns routes sorted by ``net_apy_pct`` descending.

        Parameters
        ----------
        source_asset
            The asset currently held (e.g. ``"USDC"``).
        amount_usd
            Size of the position in USD.
        side
            Filter to SUPPLY or BORROW only. ``None`` returns both.
        umbrella
            Restrict target opportunities to this umbrella group.
        """
        cfg = self._config
        candidates = self._filter_opportunities(side=side, umbrella=umbrella)
        reachable = self._reachable_assets(source_asset)
        routes: list[YieldRoute] = []

        for opp in candidates:
            target = opp.asset_id

            # Find conversion path
            if target == source_asset:
                path: list[ConversionEdge] = []
                cost_info = _empty_cost()
            elif target in reachable:
                result = self._router.cheapest_path(
                    source_asset, target, amount_usd=amount_usd,
                    max_hops=cfg.max_conversion_steps,
                )
                if result is None:
                    continue
                path, cost_info = result
            else:
                continue

            # Conversion cost filter
            conv_bps = cost_info["total_cost_bps"]
            if conv_bps > cfg.max_conversion_cost_bps:
                continue

            # Capacity check
            max_deploy = self._max_deployable(opp, amount_usd)
            if max_deploy <= 0:
                continue

            # Rate impact
            rate_impact_bps = 0.0
            post_apy: float | None = None
            if cfg.rate_impact_enabled and opp.rate_model is not None:
                rate_impact_bps, post_apy = self._estimate_rate_impact(
                    opp, max_deploy,
                )

            # Net APY calculation
            gross_apy = opp.total_apy_pct
            annualized_conv = self._annualize_cost(conv_bps, cfg.holding_period_days)

            if opp.side == OpportunitySide.SUPPLY:
                net_apy = gross_apy - annualized_conv - (rate_impact_bps / 100.0)
            else:
                # Borrow: user pays the rate, so lower is better.
                # net_apy is negative (cost) minus conversion overhead.
                net_apy = -(gross_apy + annualized_conv + (rate_impact_bps / 100.0))

            if net_apy < cfg.min_apy_pct and opp.side == OpportunitySide.SUPPLY:
                continue

            # Risk assessment
            risk_flags = self._assess_risk(opp, cost_info, amount_usd)
            risk_score = self._compute_risk_score(risk_flags)
            if risk_score > cfg.max_risk_score:
                continue

            # Collateral (borrow routes)
            collateral = None
            if opp.side == OpportunitySide.BORROW:
                collateral = self._best_collateral(opp, source_asset, amount_usd)

            route = YieldRoute(
                opportunity=opp,
                source_asset=source_asset,
                target_asset=target,
                side=opp.side,
                conversion_path=path,
                conversion_steps=cost_info["num_hops"],
                conversion_cost_bps=conv_bps,
                conversion_gas_usd=cost_info["total_gas_usd"],
                conversion_time_min_seconds=cost_info["min_duration_seconds"],
                conversion_time_max_seconds=cost_info["max_duration_seconds"],
                is_conversion_deterministic=cost_info["is_deterministic"],
                gross_apy_pct=gross_apy,
                net_apy_pct=net_apy,
                annualized_conversion_cost_pct=annualized_conv,
                max_deployable_usd=max_deploy,
                capacity_limited=cost_info["has_capacity_limit"] or opp.is_capacity_capped,
                rate_impact_bps=rate_impact_bps,
                post_deposit_apy_pct=post_apy,
                risk_flags=risk_flags,
                risk_score=risk_score,
                collateral=collateral,
            )
            routes.append(route)

        routes.sort(key=lambda r: r.net_apy_pct, reverse=True)
        return routes

    def find_best_route(
        self,
        source_asset: str,
        amount_usd: float,
        *,
        side: OpportunitySide | None = None,
        umbrella: AssetUmbrella | None = None,
    ) -> YieldRoute | None:
        """Return the single highest net-APY route, or ``None``."""
        routes = self.find_routes(
            source_asset, amount_usd, side=side, umbrella=umbrella,
        )
        return routes[0] if routes else None

    # ------------------------------------------------------------------
    # Rate impact estimation (kink model)
    # ------------------------------------------------------------------

    def _estimate_rate_impact(
        self,
        opp: MarketOpportunity,
        deposit_usd: float,
    ) -> tuple[float, float | None]:
        """Estimate how much the rate moves if *deposit_usd* enters the pool.

        Returns ``(impact_bps, post_deposit_apy_pct)``.

        Uses the standard kink interest rate model::

            if utilization <= optimal:
                rate = base_rate + slope1 * (utilization / optimal)
            else:
                rate = base_rate + slope1 + slope2 * ((utilization - optimal) / (1 - optimal))
        """
        rm = opp.rate_model
        if rm is None:
            return 0.0, None

        # Need rate model params
        if rm.optimal_utilization_pct is None or rm.slope1_pct is None:
            return 0.0, None

        optimal = rm.optimal_utilization_pct / 100.0
        base_rate = (rm.base_rate_pct or 0.0) / 100.0
        slope1 = rm.slope1_pct / 100.0
        slope2 = (rm.slope2_pct or 0.0) / 100.0

        # Current pool state
        current_util = (opp.liquidity.utilization_rate_pct or 0.0) / 100.0
        total_supplied_usd = opp.total_supplied_usd or opp.tvl_usd or 0.0

        if total_supplied_usd <= 0:
            return 0.0, None

        total_borrowed_usd = opp.total_borrowed_usd or (current_util * total_supplied_usd)

        # Post-deposit utilization
        if opp.side == OpportunitySide.SUPPLY:
            new_supplied = total_supplied_usd + deposit_usd
            new_util = total_borrowed_usd / new_supplied if new_supplied > 0 else 0.0
        else:
            # Borrow increases utilization
            new_borrowed = total_borrowed_usd + deposit_usd
            new_util = new_borrowed / total_supplied_usd if total_supplied_usd > 0 else 1.0

        new_util = min(new_util, 1.0)

        # Compute rates via kink model
        pre_rate = _kink_rate(current_util, optimal, base_rate, slope1, slope2)
        post_rate = _kink_rate(new_util, optimal, base_rate, slope1, slope2)

        # For supply: supply rate = borrow rate * utilization * (1 - reserve_factor)
        # Simplified: we use the same model for both sides, impact is the delta
        if opp.side == OpportunitySide.SUPPLY:
            pre_supply = pre_rate * current_util
            post_supply = post_rate * new_util
            impact_pct = pre_supply - post_supply  # rate decreases when supply increases
            post_apy = max(0.0, opp.total_apy_pct - impact_pct * 100.0)
        else:
            impact_pct = post_rate - pre_rate  # rate increases when borrows increase
            post_apy = opp.total_apy_pct + impact_pct * 100.0

        impact_bps = abs(impact_pct) * 10_000.0
        return impact_bps, post_apy

    # ------------------------------------------------------------------
    # Risk assessment
    # ------------------------------------------------------------------

    def _assess_risk(
        self,
        opp: MarketOpportunity,
        cost_info: dict,
        amount_usd: float,
    ) -> list[str]:
        """Return a list of risk flag strings for the opportunity."""
        flags: list[str] = []

        # 1. Low or unknown TVL
        if opp.tvl_usd is None:
            flags.append("LOW_TVL")
        elif opp.tvl_usd < 1_000_000:
            flags.append("LOW_TVL")

        # 2. High utilization
        util = opp.liquidity.utilization_rate_pct
        if util is not None and util > 90.0:
            flags.append("HIGH_UTILIZATION")

        # 3. Capacity capped
        if opp.is_capacity_capped:
            flags.append("CAPACITY_CAPPED")

        # 4. Withdrawal queue
        if opp.liquidity.has_withdrawal_queue:
            flags.append("WITHDRAWAL_QUEUE")

        # 5. Lockup
        if opp.liquidity.has_lockup:
            flags.append("LOCKUP")

        # 6. Non-deterministic conversion
        if not cost_info["is_deterministic"]:
            flags.append("NON_DETERMINISTIC_CONVERSION")

        # 7. High conversion cost
        if cost_info["total_cost_bps"] > 50.0:
            flags.append("HIGH_CONVERSION_COST")

        # 8. Rate model near kink
        if opp.rate_model is not None and opp.liquidity.utilization_rate_pct is not None:
            optimal = opp.rate_model.optimal_utilization_pct
            if optimal is not None:
                current_util = opp.liquidity.utilization_rate_pct
                if current_util > optimal * 0.9:
                    flags.append("RATE_MODEL_KINK")

        # 9. Low available liquidity relative to position
        avail = opp.liquidity.available_liquidity_usd
        if avail is not None and amount_usd > 0 and avail < amount_usd * 2:
            flags.append("LOW_LIQUIDITY")

        # 10. Stale data
        if opp.data_freshness_seconds > 3600:
            flags.append("STALE_DATA")

        # 12. Anomalously high APY (likely bad data)
        if opp.total_apy_pct > 100.0 and not opp.is_pendle:
            flags.append("ANOMALOUS_APY")

        # 11. Isolated collateral (borrow side)
        if opp.collateral_options:
            if all(c.is_isolated for c in opp.collateral_options):
                flags.append("ISOLATED_COLLATERAL")

        return flags

    def _compute_risk_score(self, flags: list[str]) -> float:
        """Compute composite 0-1 risk score from flags."""
        if not flags:
            return 0.0
        total = sum(_RISK_WEIGHTS.get(f, 0.05) for f in flags)
        return min(total, 1.0)

    # ------------------------------------------------------------------
    # Collateral assessment (borrow routes)
    # ------------------------------------------------------------------

    def _best_collateral(
        self,
        opp: MarketOpportunity,
        source_asset: str,
        amount_usd: float,
    ) -> CollateralRequirement | None:
        """Pick the best collateral option for a borrow opportunity."""
        if not opp.collateral_options:
            return None

        best: CollateralRequirement | None = None
        best_cost: float = float("inf")

        for col in opp.collateral_options:
            # Skip if no remaining capacity
            if col.remaining_capacity is not None and col.remaining_capacity <= 0:
                continue

            ltv = col.max_ltv_pct / 100.0 if col.max_ltv_pct > 0 else 0.5
            liq_ltv = col.liquidation_ltv_pct / 100.0

            # Collateral needed = borrow / LTV
            collateral_usd = amount_usd / ltv

            # Conversion cost to get collateral asset
            conv_cost_bps = 0.0
            if col.asset_id != source_asset:
                result = self._router.cheapest_path(
                    source_asset, col.asset_id, amount_usd=collateral_usd,
                )
                if result is None:
                    continue
                _, cost = result
                conv_cost_bps = cost["total_cost_bps"]

            # Opportunity cost: collateral is locked, not earning
            # Estimate as the best supply APY for that asset
            opp_cost_apy = self._best_supply_apy(col.asset_id)

            buffer_pct = (liq_ltv - ltv) / liq_ltv * 100.0 if liq_ltv > 0 else 0.0

            total_cost = conv_cost_bps + opp_cost_apy * 100.0  # rough ranking metric

            req = CollateralRequirement(
                collateral_asset=col.asset_id,
                collateral_amount_usd=collateral_usd,
                max_ltv_pct=col.max_ltv_pct,
                liquidation_ltv_pct=col.liquidation_ltv_pct,
                liquidation_buffer_pct=round(buffer_pct, 2),
                conversion_cost_bps=round(conv_cost_bps, 2),
                opportunity_cost_apy_pct=round(opp_cost_apy, 4),
            )

            if total_cost < best_cost:
                best_cost = total_cost
                best = req

        return best

    def _best_supply_apy(self, asset_id: str) -> float:
        """Find the highest supply APY available for *asset_id*."""
        best = 0.0
        for opp in self._opportunities:
            if opp.side == OpportunitySide.SUPPLY and opp.asset_id == asset_id:
                if opp.total_apy_pct > best:
                    best = opp.total_apy_pct
        return best

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _filter_opportunities(
        self,
        *,
        side: OpportunitySide | None = None,
        umbrella: AssetUmbrella | None = None,
    ) -> list[MarketOpportunity]:
        """Pre-filter opportunities by config thresholds."""
        cfg = self._config
        result: list[MarketOpportunity] = []

        for opp in self._opportunities:
            if side is not None and opp.side != side:
                continue
            if umbrella is not None and opp.umbrella_group != umbrella.value:
                continue
            if cfg.exclude_amm_lp and opp.is_amm_lp:
                continue
            if cfg.exclude_pendle and opp.is_pendle:
                continue
            if opp.tvl_usd is not None and opp.tvl_usd < cfg.min_tvl_usd:
                continue
            result.append(opp)

        return result

    def _reachable_assets(self, source_asset: str) -> set[str]:
        """All assets reachable from *source_asset* within max hops."""
        reachable: set[str] = {source_asset}

        # Add fungible group (zero-cost equivalents)
        for fid in get_fungible_group(source_asset):
            reachable.add(fid)

        # Add umbrella peers reachable via conversion graph
        asset_def = ASSET_REGISTRY.get(source_asset)
        if asset_def is not None:
            for peer in get_umbrella_assets(asset_def.umbrella):
                paths = self._router.find_conversion_path(
                    source_asset, peer.canonical_id,
                    max_hops=self._config.max_conversion_steps,
                )
                if paths:
                    reachable.add(peer.canonical_id)

        return reachable

    def _max_deployable(self, opp: MarketOpportunity, amount_usd: float) -> float:
        """Compute the maximum deployable amount considering capacity and pool share."""
        cfg = self._config
        limits: list[float] = [amount_usd]

        # Pool share limit
        if opp.tvl_usd is not None and opp.tvl_usd > 0:
            pool_limit = opp.tvl_usd * (cfg.max_pool_share_pct / 100.0)
            limits.append(pool_limit)

        # Explicit capacity remaining
        if opp.capacity_remaining is not None:
            limits.append(opp.capacity_remaining)

        # Available liquidity (for supply, this is how much more the pool accepts)
        if opp.liquidity.available_liquidity_usd is not None:
            limits.append(opp.liquidity.available_liquidity_usd)

        return max(0.0, min(limits))

    @staticmethod
    def _annualize_cost(cost_bps: float, holding_period_days: int) -> float:
        """Convert a one-time bps cost to an annualised percentage.

        For example, 10 bps one-time over a 90 day hold = 10/10000 * 365/90
        = 0.0406% annualised.
        """
        if holding_period_days <= 0:
            return 0.0
        return (cost_bps / 100.0) * (_DAYS_PER_YEAR / holding_period_days)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _kink_rate(
    utilization: float,
    optimal: float,
    base_rate: float,
    slope1: float,
    slope2: float,
) -> float:
    """Compute the interest rate at a given utilization via the kink model."""
    if optimal <= 0:
        return base_rate
    if utilization <= optimal:
        return base_rate + slope1 * (utilization / optimal)
    excess = (utilization - optimal) / (1.0 - optimal) if optimal < 1.0 else 0.0
    return base_rate + slope1 + slope2 * excess


def _empty_cost() -> dict:
    """Return a zero-cost dict matching ConversionRouter.estimate_conversion_cost."""
    return {
        "total_gas_usd": 0.0,
        "total_fee_bps": 0.0,
        "total_slippage_bps": 0.0,
        "total_cost_bps": 0.0,
        "net_cost_usd": 0.0,
        "min_duration_seconds": 0,
        "max_duration_seconds": 0,
        "num_hops": 0,
        "is_deterministic": True,
        "has_capacity_limit": False,
    }
