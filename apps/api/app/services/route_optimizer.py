"""
Route optimizer — compares four sourcing strategies for a target asset.

Design principles (mirrors borrow_demand.py):
- Pure function: `optimize(inputs)` contains zero I/O.
- Every cost component names its data source or marks itself as an assumption.
- Assumptions are explicit, grouped in RouteAssumptions, and override-able.
- Hooks for manual metadata are clearly marked with HOOK comments.

Route taxonomy
──────────────
  direct_borrow          — Borrow the target asset from a lending protocol.
  stable_borrow_spot     — Borrow stablecoin, buy target asset on spot.
  wrapper_transform      — Borrow a related asset and convert (wrap/stake/unwrap).
  synthetic_hedge        — Borrow stablecoin + hold long perp (synthetic exposure).

Cost model
──────────
  All costs are in basis points (1 bps = 0.01%) annualized unless noted.
  Annualized bps = APY% × 100.
  Final cost = sum of CostComponent.value_bps for each component.

Max-executable-size model
──────────────────────────
  max_executable_usd = Σ min(avail_i, tvl_i × max_pool_share)
  Rationale: the desk won't deploy > max_pool_share of any single pool
  to avoid becoming the marginal price-setter.

Ranking
───────
  Primary key: effective_cost_bps (ascending, lower = better).
  Penalty: if max_executable_usd < request_size_usd, add
           SIZE_SHORTFALL_PENALTY_BPS × (1 − size_coverage) to effective cost.
  Routes that cannot execute at all (max_executable_usd ≤ 0) are marked
  infeasible and ranked last.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Literal


# ─────────────────────────────────────────────────────────────────────────────
# Inputs
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class LendingMarket:
    """Single lending market snapshot (protocol × asset × chain)."""

    protocol: str
    market: str        # DeFiLlama pool name
    chain: str | None
    borrow_apy: float | None      # percent
    supply_apy: float | None      # percent
    utilization: float | None     # 0-1
    available_liquidity_usd: float | None
    tvl_usd: float | None
    snapshot_at: datetime | None


@dataclass
class TransformPath:
    """
    A single asset conversion leg.

    HOOK: populate from DB-backed metadata table when manual overrides
    are needed. Static values defined in route_optimizer_loader.py.
    """

    from_asset: str
    to_asset: str
    transform_type: str    # "stake" | "unstake" | "wrap" | "unwrap" | "bridge"
    fee_bps: float         # one-way fee in bps
    slippage_bps: float    # estimated market slippage (additional to fee)
    latency_seconds: float | None
    unbonding_days: float | None   # lock / exit queue if any
    capacity_usd: float | None     # None = unlimited for practical purposes


@dataclass
class RouteAssumptions:
    """
    Static desk parameters that govern capacity and cost estimates.

    HOOK: all fields can be overridden per-request via the API body.
    Each default is documented with its source/rationale.
    """

    # Fraction of a single lending pool the desk will use.
    # Rationale: avoid being > 25% of any pool to limit price impact and
    # avoid a single counterparty.
    max_pool_share: float = 0.25

    # Fraction of open interest the desk will represent on a perp venue.
    # Rationale: > 5% OI creates meaningful mark-price risk.
    max_oi_share: float = 0.05

    # Spot purchase slippage for the stablecoin→spot route, in bps.
    # Assumption: institutional limit-order at 10 bps for BTC/ETH/SOL
    # at sizes up to $50M. Desk should adjust for larger sizes.
    spot_slippage_bps: float = 10.0

    # Additional premium applied to the synthetic route to account for
    # funding rate variance. Funding is not locked; desk pays/receives daily.
    # Assumption: 100 bps buffer covers ~1σ of 30d funding volatility.
    funding_variance_premium_bps: float = 100.0

    # Extra slippage on wrapper routes beyond the protocol's fee.
    # Accounts for DEX liquidity when redeeming (e.g. stETH/ETH curve pool).
    wrapper_extra_slippage_bps: float = 5.0

    # Unbonding risk premium per day of mandatory lock.
    # Assumption: 2 bps/day captures reinvestment risk and lock-up optionality.
    unbonding_bps_per_day: float = 2.0

    # Minimum cost threshold below which a route is considered trivially cheap.
    min_meaningful_cost_bps: float = 1.0

    # Penalty (in bps) per unit of size shortfall when ranking.
    # Adds effective cost to routes that can't fill the requested size.
    size_shortfall_penalty_bps: float = 200.0


@dataclass
class RouteOptimizerInputs:
    target_asset: str
    request_size_usd: float        # desired notional in USD

    # Lending markets for the target asset (direct borrow leg)
    target_markets: list[LendingMarket] = field(default_factory=list)

    # Lending markets for stablecoins (USDC, USDT)
    stable_markets: list[LendingMarket] = field(default_factory=list)

    # Derivatives for the target asset (synthetic route)
    funding_rate: float | None = None       # raw 8h rate (e.g. 0.0003)
    basis_annualized: float | None = None   # decimal (0.10 = 10%)
    open_interest_usd: float | None = None

    # Lending markets for transform source assets
    # e.g. {"stETH": [...], "WBTC": [...]}
    # HOOK: populated by loader from DB; add more paths via manual metadata.
    transform_source_markets: dict[str, list[LendingMarket]] = field(
        default_factory=dict
    )

    # Available transform paths INTO the target asset
    transforms_to_target: list[TransformPath] = field(default_factory=list)

    # Desk parameters (all overridable)
    assumptions: RouteAssumptions = field(default_factory=RouteAssumptions)


# ─────────────────────────────────────────────────────────────────────────────
# Outputs
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class CostComponent:
    name: str
    value_bps: float    # positive = cost, negative = income
    source: str         # data source label
    is_assumption: bool # True if derived from RouteAssumptions rather than live data


@dataclass
class Bottleneck:
    constraint: str
    limiting_factor: str
    severity: Literal["hard", "soft"]   # hard = caps executable size; soft = notable risk
    value: float | None
    value_unit: str


@dataclass
class Route:
    route_type: Literal[
        "direct_borrow", "stable_borrow_spot",
        "wrapper_transform", "synthetic_hedge",
    ]
    display_name: str
    description: str

    cost_components: list[CostComponent]
    total_cost_bps: float          # sum of cost_components.value_bps
    max_executable_usd: float      # conservative capacity
    feasible: bool                 # False if max_executable_usd ≤ 0

    bottlenecks: list[Bottleneck]
    assumptions_used: list[str]    # human-readable assumption statements

    # Set by ranker
    rank: int = 0
    effective_cost_bps: float = 0.0   # total_cost_bps + size-shortfall penalty
    ranking_rationale: str = ""


@dataclass
class RouteOptimizerResult:
    target_asset: str
    request_size_usd: float
    routes: list[Route]           # sorted by rank
    recommended_route: str        # route_type of rank-1
    summary: str
    computed_at: datetime
    assumptions: RouteAssumptions


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _max_borrowable(
    markets: list[LendingMarket],
    max_pool_share: float,
) -> float:
    """Sum each market's available liquidity, capped at max_pool_share of TVL."""
    total = 0.0
    for m in markets:
        avail = m.available_liquidity_usd or 0.0
        if m.tvl_usd and m.tvl_usd > 0:
            cap = m.tvl_usd * max_pool_share
            total += min(avail, cap)
        else:
            total += avail
    return total


def _best_borrow_market(
    markets: list[LendingMarket],
) -> LendingMarket | None:
    valid = [m for m in markets if m.borrow_apy is not None and (m.available_liquidity_usd or 0) > 0]
    if not valid:
        return None
    return min(valid, key=lambda m: m.borrow_apy)  # type: ignore[return-value]


def _funding_annualized_pct(funding_rate_8h: float) -> float:
    """Convert raw 8h funding rate to annualized percent."""
    return funding_rate_8h * 3 * 365 * 100


# ─────────────────────────────────────────────────────────────────────────────
# Route builders
# ─────────────────────────────────────────────────────────────────────────────


def _build_direct_borrow(inputs: RouteOptimizerInputs) -> Route:
    ass = inputs.assumptions
    markets = inputs.target_markets
    best = _best_borrow_market(markets)
    max_usd = _max_borrowable(markets, ass.max_pool_share)

    cost_components: list[CostComponent] = []
    bottlenecks: list[Bottleneck] = []
    assumptions_used: list[str] = [
        f"Max pool share: {ass.max_pool_share * 100:.0f}% of any single market TVL",
    ]

    if best and best.borrow_apy is not None:
        cost_components.append(CostComponent(
            name="borrow_apy",
            value_bps=best.borrow_apy * 100,
            source=f"DeFiLlama / {best.protocol}",
            is_assumption=False,
        ))
        # Utilization bottleneck
        if best.utilization is not None and best.utilization > 0.80:
            sev: Literal["hard", "soft"] = "hard" if best.utilization > 0.95 else "soft"
            bottlenecks.append(Bottleneck(
                constraint="High market utilization",
                limiting_factor=f"{best.protocol} at {best.utilization * 100:.1f}% utilization",
                severity=sev,
                value=best.utilization * 100,
                value_unit="%",
            ))
    else:
        cost_components.append(CostComponent(
            name="borrow_apy",
            value_bps=0.0,
            source="no data",
            is_assumption=True,
        ))

    # Capacity bottleneck
    if max_usd < inputs.request_size_usd:
        bottlenecks.append(Bottleneck(
            constraint="Insufficient available liquidity",
            limiting_factor=f"${max_usd / 1e6:.1f}M available across {len(markets)} markets",
            severity="hard",
            value=max_usd,
            value_unit="USD",
        ))

    total_bps = sum(c.value_bps for c in cost_components)

    return Route(
        route_type="direct_borrow",
        display_name="Direct Borrow",
        description=(
            f"Borrow {inputs.target_asset} directly from the cheapest lending "
            f"market ({best.protocol if best else 'n/a'})."
        ),
        cost_components=cost_components,
        total_cost_bps=total_bps,
        max_executable_usd=max_usd,
        feasible=max_usd > 0 and bool(best),
        bottlenecks=bottlenecks,
        assumptions_used=assumptions_used,
    )


def _build_stable_borrow_spot(inputs: RouteOptimizerInputs) -> Route:
    ass = inputs.assumptions
    stable_best = _best_borrow_market(inputs.stable_markets)
    max_stable = _max_borrowable(inputs.stable_markets, ass.max_pool_share)
    # Spot purchase: assume unlimited depth up to request_size for now
    # HOOK: wire to a CEX/DEX depth oracle for size-dependent slippage
    max_usd = max_stable

    cost_components: list[CostComponent] = []
    bottlenecks: list[Bottleneck] = []
    assumptions_used: list[str] = [
        f"Spot slippage: {ass.spot_slippage_bps:.0f} bps (institutional limit order, ≤$50M)",
        f"Max pool share: {ass.max_pool_share * 100:.0f}% of stablecoin market TVL",
        "Spot market depth assumed adequate at requested size — HOOK: replace with live depth data",
    ]

    if inputs.target_asset in ("USDC", "USDT", "DAI", "FRAX"):
        # Circular / not applicable
        return Route(
            route_type="stable_borrow_spot",
            display_name="Stablecoin Borrow → Spot",
            description="Not applicable: target asset is itself a stablecoin.",
            cost_components=[],
            total_cost_bps=9999.0,
            max_executable_usd=0.0,
            feasible=False,
            bottlenecks=[Bottleneck(
                constraint="Circular route",
                limiting_factor="Target asset is a stablecoin; cannot buy it via itself",
                severity="hard",
                value=None,
                value_unit="",
            )],
            assumptions_used=[],
        )

    if stable_best and stable_best.borrow_apy is not None:
        cost_components.append(CostComponent(
            name="stablecoin_borrow_apy",
            value_bps=stable_best.borrow_apy * 100,
            source=f"DeFiLlama / {stable_best.protocol}",
            is_assumption=False,
        ))
    cost_components.append(CostComponent(
        name="spot_slippage",
        value_bps=ass.spot_slippage_bps,
        source="RouteAssumptions.spot_slippage_bps",
        is_assumption=True,
    ))

    if max_usd < inputs.request_size_usd:
        bottlenecks.append(Bottleneck(
            constraint="Stablecoin liquidity",
            limiting_factor=f"${max_usd / 1e6:.1f}M available stablecoin across tracked markets",
            severity="hard",
            value=max_usd,
            value_unit="USD",
        ))
    if stable_best and stable_best.utilization is not None and stable_best.utilization > 0.85:
        bottlenecks.append(Bottleneck(
            constraint="High stablecoin utilization",
            limiting_factor=f"{stable_best.protocol} stablecoin pool at {stable_best.utilization * 100:.1f}%",
            severity="soft",
            value=stable_best.utilization * 100,
            value_unit="%",
        ))

    total_bps = sum(c.value_bps for c in cost_components)
    return Route(
        route_type="stable_borrow_spot",
        display_name="Stablecoin Borrow → Spot",
        description=(
            f"Borrow {stable_best.protocol if stable_best else 'stablecoin'} "
            f"USDC/USDT, then purchase {inputs.target_asset} on spot."
        ),
        cost_components=cost_components,
        total_cost_bps=total_bps,
        max_executable_usd=max_usd,
        feasible=max_usd > 0 and bool(stable_best),
        bottlenecks=bottlenecks,
        assumptions_used=assumptions_used,
    )


def _build_wrapper_transform(inputs: RouteOptimizerInputs) -> Route:
    ass = inputs.assumptions
    target = inputs.target_asset

    # Find the cheapest (transform_fee + borrow_of_source) path
    best_cost_bps: float | None = None
    best_path: TransformPath | None = None
    best_source_market: LendingMarket | None = None
    best_max_usd: float = 0.0
    all_cost_components: list[CostComponent] = []
    candidate_assumptions: list[str] = []

    for path in inputs.transforms_to_target:
        source_markets = inputs.transform_source_markets.get(path.from_asset, [])
        source_best = _best_borrow_market(source_markets)
        if not source_best or source_best.borrow_apy is None:
            continue

        transform_total_fee = path.fee_bps + path.slippage_bps + ass.wrapper_extra_slippage_bps
        unbonding_premium = (path.unbonding_days or 0) * ass.unbonding_bps_per_day
        path_cost = source_best.borrow_apy * 100 + transform_total_fee + unbonding_premium

        if best_cost_bps is None or path_cost < best_cost_bps:
            best_cost_bps = path_cost
            best_path = path
            best_source_market = source_best
            best_max_usd = min(
                _max_borrowable(source_markets, ass.max_pool_share),
                path.capacity_usd if path.capacity_usd else 1e12,
            )
            all_cost_components = [
                CostComponent(
                    name="source_borrow_apy",
                    value_bps=source_best.borrow_apy * 100,
                    source=f"DeFiLlama / {source_best.protocol}",
                    is_assumption=False,
                ),
                CostComponent(
                    name="transform_fee",
                    value_bps=path.fee_bps,
                    source=f"TransformPath {path.from_asset}→{path.to_asset}",
                    is_assumption=False,
                ),
                CostComponent(
                    name="transform_slippage",
                    value_bps=path.slippage_bps + ass.wrapper_extra_slippage_bps,
                    source="RouteAssumptions.wrapper_extra_slippage_bps",
                    is_assumption=True,
                ),
            ]
            if path.unbonding_days and path.unbonding_days > 0:
                all_cost_components.append(CostComponent(
                    name="unbonding_risk_premium",
                    value_bps=unbonding_premium,
                    source=f"RouteAssumptions.unbonding_bps_per_day × {path.unbonding_days:.1f}d",
                    is_assumption=True,
                ))
            candidate_assumptions = [
                f"Wrapper extra slippage: {ass.wrapper_extra_slippage_bps:.0f} bps",
                f"Unbonding premium: {ass.unbonding_bps_per_day:.0f} bps/day",
                f"Max pool share: {ass.max_pool_share * 100:.0f}% of source market TVL",
            ]

    if best_path is None:
        return Route(
            route_type="wrapper_transform",
            display_name="Wrapper / Transform",
            description=f"No transform path available into {target}.",
            cost_components=[],
            total_cost_bps=9999.0,
            max_executable_usd=0.0,
            feasible=False,
            bottlenecks=[Bottleneck(
                constraint="No transform path",
                limiting_factor=f"No known conversion into {target} with tracked source assets",
                severity="hard",
                value=None,
                value_unit="",
            )],
            assumptions_used=["HOOK: add TransformPath entries to metadata for this asset"],
        )

    bottlenecks: list[Bottleneck] = []
    if best_max_usd < inputs.request_size_usd:
        bottlenecks.append(Bottleneck(
            constraint="Source asset liquidity",
            limiting_factor=(
                f"${best_max_usd / 1e6:.1f}M available in "
                f"{best_path.from_asset} markets"
            ),
            severity="hard",
            value=best_max_usd,
            value_unit="USD",
        ))
    if best_path.unbonding_days and best_path.unbonding_days > 0:
        bottlenecks.append(Bottleneck(
            constraint="Transform exit latency",
            limiting_factor=(
                f"{best_path.transform_type} path has {best_path.unbonding_days:.1f}d "
                "unbonding / exit queue"
            ),
            severity="soft",
            value=best_path.unbonding_days,
            value_unit="days",
        ))
    if best_path.latency_seconds and best_path.latency_seconds > 3600:
        bottlenecks.append(Bottleneck(
            constraint="Transform settlement latency",
            limiting_factor=f"{best_path.latency_seconds / 3600:.1f}h settlement time",
            severity="soft",
            value=best_path.latency_seconds / 3600,
            value_unit="hours",
        ))

    total_bps = sum(c.value_bps for c in all_cost_components)
    return Route(
        route_type="wrapper_transform",
        display_name="Wrapper / Transform",
        description=(
            f"Borrow {best_path.from_asset} ({best_source_market.protocol if best_source_market else 'n/a'}), "
            f"then {best_path.transform_type} into {best_path.to_asset}."
        ),
        cost_components=all_cost_components,
        total_cost_bps=total_bps,
        max_executable_usd=best_max_usd,
        feasible=best_max_usd > 0,
        bottlenecks=bottlenecks,
        assumptions_used=candidate_assumptions,
    )


def _build_synthetic_hedge(inputs: RouteOptimizerInputs) -> Route:
    ass = inputs.assumptions
    target = inputs.target_asset

    if target in ("USDC", "USDT", "DAI", "FRAX"):
        return Route(
            route_type="synthetic_hedge",
            display_name="Synthetic Hedge",
            description="Not applicable: stablecoins cannot be synthetically replicated via perps.",
            cost_components=[],
            total_cost_bps=9999.0,
            max_executable_usd=0.0,
            feasible=False,
            bottlenecks=[Bottleneck(
                constraint="Not applicable",
                limiting_factor="Target asset is a stablecoin; synthetic perp hedge unavailable",
                severity="hard",
                value=None,
                value_unit="",
            )],
            assumptions_used=[],
        )

    if inputs.funding_rate is None and inputs.basis_annualized is None:
        return Route(
            route_type="synthetic_hedge",
            display_name="Synthetic Hedge",
            description=f"No derivatives data for {target}; synthetic route not assessable.",
            cost_components=[],
            total_cost_bps=9999.0,
            max_executable_usd=0.0,
            feasible=False,
            bottlenecks=[Bottleneck(
                constraint="Missing derivatives data",
                limiting_factor="No Velo funding / OI data available",
                severity="hard",
                value=None,
                value_unit="",
            )],
            assumptions_used=["HOOK: connect Velo ingestion to populate funding/OI"],
        )

    stable_best = _best_borrow_market(inputs.stable_markets)
    max_stable = _max_borrowable(inputs.stable_markets, ass.max_pool_share)
    oi_headroom = (inputs.open_interest_usd or 0) * ass.max_oi_share
    max_usd = min(max_stable, oi_headroom) if oi_headroom > 0 else max_stable

    cost_components: list[CostComponent] = []
    bottlenecks: list[Bottleneck] = []
    assumptions_used: list[str] = [
        f"Max OI share: {ass.max_oi_share * 100:.0f}% of open interest",
        f"Funding variance premium: {ass.funding_variance_premium_bps:.0f} bps buffer",
        f"Max pool share: {ass.max_pool_share * 100:.0f}% of stablecoin market TVL",
        "Synthetic exposure assumes counterparty accepts mark-to-market perp PnL as equivalent to spot — HOOK: verify with desk policy",
    ]

    if stable_best and stable_best.borrow_apy is not None:
        cost_components.append(CostComponent(
            name="stablecoin_borrow_apy",
            value_bps=stable_best.borrow_apy * 100,
            source=f"DeFiLlama / {stable_best.protocol}",
            is_assumption=False,
        ))

    if inputs.funding_rate is not None:
        fund_ann_pct = _funding_annualized_pct(inputs.funding_rate)
        # Long perp pays funding when positive; receives when negative
        cost_components.append(CostComponent(
            name="perpetual_funding",
            value_bps=fund_ann_pct * 100,   # convert % to bps
            source="Velo / funding_rate",
            is_assumption=False,
        ))
    elif inputs.basis_annualized is not None:
        # Use basis as funding proxy
        basis_bps = inputs.basis_annualized * 100 * 100
        cost_components.append(CostComponent(
            name="basis_proxy_funding",
            value_bps=basis_bps,
            source="Velo / basis_annualized (proxy for funding)",
            is_assumption=False,
        ))

    cost_components.append(CostComponent(
        name="funding_variance_premium",
        value_bps=ass.funding_variance_premium_bps,
        source="RouteAssumptions.funding_variance_premium_bps",
        is_assumption=True,
    ))

    if oi_headroom > 0 and oi_headroom < inputs.request_size_usd:
        bottlenecks.append(Bottleneck(
            constraint="OI headroom",
            limiting_factor=(
                f"${oi_headroom / 1e6:.1f}M available OI headroom "
                f"({ass.max_oi_share * 100:.0f}% of ${(inputs.open_interest_usd or 0) / 1e6:.0f}M total OI)"
            ),
            severity="hard",
            value=oi_headroom,
            value_unit="USD",
        ))
    if max_stable < inputs.request_size_usd:
        bottlenecks.append(Bottleneck(
            constraint="Stablecoin liquidity",
            limiting_factor=f"${max_stable / 1e6:.1f}M available stablecoin",
            severity="hard",
            value=max_stable,
            value_unit="USD",
        ))

    total_bps = sum(c.value_bps for c in cost_components)
    fund_summary = (
        f"funding at {_funding_annualized_pct(inputs.funding_rate):.2f}% ann."
        if inputs.funding_rate is not None
        else "no funding data"
    )
    return Route(
        route_type="synthetic_hedge",
        display_name="Synthetic Hedge",
        description=(
            f"Borrow stablecoin, hold long {target} perp ({fund_summary}). "
            "Synthetic spot exposure."
        ),
        cost_components=cost_components,
        total_cost_bps=total_bps,
        max_executable_usd=max(max_usd, 0.0),
        feasible=max_usd > 0,
        bottlenecks=bottlenecks,
        assumptions_used=assumptions_used,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Ranking
# ─────────────────────────────────────────────────────────────────────────────


def _rank_routes(routes: list[Route], request_size_usd: float, ass: RouteAssumptions) -> list[Route]:
    for route in routes:
        if not route.feasible:
            route.effective_cost_bps = 1e9  # infeasible routes go last
            continue
        shortfall = max(0.0, request_size_usd - route.max_executable_usd)
        shortfall_ratio = shortfall / request_size_usd if request_size_usd > 0 else 0.0
        route.effective_cost_bps = (
            route.total_cost_bps + shortfall_ratio * ass.size_shortfall_penalty_bps
        )

    ranked = sorted(routes, key=lambda r: r.effective_cost_bps)
    for i, r in enumerate(ranked):
        r.rank = i + 1

    # Rationale sentences
    for r in ranked:
        if not r.feasible:
            r.ranking_rationale = "Route is infeasible or not applicable for this asset."
            continue
        shortfall_msg = ""
        if r.max_executable_usd < request_size_usd:
            shortfall_msg = (
                f" Size is limited to ${r.max_executable_usd / 1e6:.1f}M "
                f"vs ${request_size_usd / 1e6:.1f}M requested."
            )
        r.ranking_rationale = (
            f"Rank {r.rank}: estimated cost {r.total_cost_bps:.0f} bps ann. "
            f"(effective {r.effective_cost_bps:.0f} bps with size penalty)."
            + shortfall_msg
        )

    return ranked


# ─────────────────────────────────────────────────────────────────────────────
# Summary builder
# ─────────────────────────────────────────────────────────────────────────────


def _build_summary(
    target: str,
    request_usd: float,
    routes: list[Route],
) -> str:
    feasible = [r for r in routes if r.feasible]
    if not feasible:
        return (
            f"No executable route found for sourcing {target} at "
            f"${request_usd / 1e6:.1f}M. Check data freshness and market conditions."
        )

    best = feasible[0]
    second = feasible[1] if len(feasible) > 1 else None

    size_note = (
        f"Full ${request_usd / 1e6:.1f}M is executable."
        if best.max_executable_usd >= request_usd
        else f"Capacity is limited to ${best.max_executable_usd / 1e6:.1f}M; consider splitting the trade."
    )

    comparison = ""
    if second:
        spread = second.total_cost_bps - best.total_cost_bps
        comparison = (
            f" The next-best route ({second.display_name}) costs "
            f"{spread:.0f} bps more at {second.total_cost_bps:.0f} bps ann."
        )

    bottleneck_note = ""
    if best.bottlenecks:
        b = best.bottlenecks[0]
        bottleneck_note = f" Main constraint: {b.limiting_factor}."

    return (
        f"Best route for {target} is {best.display_name} at "
        f"{best.total_cost_bps:.0f} bps annualized cost."
        f"{comparison} {size_note}{bottleneck_note}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main optimizer
# ─────────────────────────────────────────────────────────────────────────────


def optimize(inputs: RouteOptimizerInputs) -> RouteOptimizerResult:
    """
    Pure function. Takes RouteOptimizerInputs, returns RouteOptimizerResult.
    No database or network calls.
    """
    routes = [
        _build_direct_borrow(inputs),
        _build_stable_borrow_spot(inputs),
        _build_wrapper_transform(inputs),
        _build_synthetic_hedge(inputs),
    ]

    ranked = _rank_routes(routes, inputs.request_size_usd, inputs.assumptions)
    recommended = next((r.route_type for r in ranked if r.feasible), ranked[0].route_type)
    summary = _build_summary(inputs.target_asset, inputs.request_size_usd, ranked)

    return RouteOptimizerResult(
        target_asset=inputs.target_asset,
        request_size_usd=inputs.request_size_usd,
        routes=ranked,
        recommended_route=recommended,
        summary=summary,
        computed_at=datetime.now(UTC),
        assumptions=inputs.assumptions,
    )
