"""
Unit tests for the route optimizer engine.

All tests use the pure `optimize()` function — no DB, no network.
Tests cover:
  - Cost formula accuracy for each route type
  - Ranking logic and size-shortfall penalty
  - Infeasible route handling
  - Assumption override propagation
  - Stablecoin target edge cases
  - Summary generation
"""

from __future__ import annotations

from app.services.route_optimizer import (
    LendingMarket,
    RouteAssumptions,
    RouteOptimizerInputs,
    TransformPath,
    optimize,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _market(
    protocol: str = "aave-v3",
    borrow_apy: float = 5.0,
    supply_apy: float = 4.0,
    utilization: float = 0.70,
    avail: float = 100_000_000,
    tvl: float = 500_000_000,
    chain: str = "Ethereum",
) -> LendingMarket:
    return LendingMarket(
        protocol=protocol,
        market="TEST",
        chain=chain,
        borrow_apy=borrow_apy,
        supply_apy=supply_apy,
        utilization=utilization,
        available_liquidity_usd=avail,
        tvl_usd=tvl,
        snapshot_at=None,
    )


def _transform(
    from_asset: str,
    to_asset: str,
    fee_bps: float = 5.0,
    slippage_bps: float = 2.0,
    unbonding_days: float | None = None,
) -> TransformPath:
    return TransformPath(
        from_asset=from_asset,
        to_asset=to_asset,
        transform_type="stake",
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        latency_seconds=12.0,
        unbonding_days=unbonding_days,
        capacity_usd=None,
    )


def _default_inputs(
    symbol: str = "ETH",
    size: float = 10_000_000,
    target_borrow_apy: float = 4.0,
    stable_borrow_apy: float = 6.0,
    funding_rate: float | None = None,
    transforms: list[TransformPath] | None = None,
    transform_source_markets: dict | None = None,
    ass: RouteAssumptions | None = None,
) -> RouteOptimizerInputs:
    return RouteOptimizerInputs(
        target_asset=symbol,
        request_size_usd=size,
        target_markets=[_market(borrow_apy=target_borrow_apy, avail=200_000_000)],
        stable_markets=[_market(protocol="morpho-blue", borrow_apy=stable_borrow_apy, avail=300_000_000)],
        funding_rate=funding_rate,
        open_interest_usd=5_000_000_000,
        transforms_to_target=transforms or [],
        transform_source_markets=transform_source_markets or {},
        assumptions=ass or RouteAssumptions(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Direct borrow
# ─────────────────────────────────────────────────────────────────────────────


def test_direct_borrow_cost():
    """Direct borrow cost = borrow_apy × 100 bps."""
    result = optimize(_default_inputs(target_borrow_apy=5.0))
    direct = next(r for r in result.routes if r.route_type == "direct_borrow")
    assert direct.total_cost_bps == 500.0


def test_direct_borrow_max_size_respects_pool_share():
    """Max size is capped at max_pool_share × TVL."""
    ass = RouteAssumptions(max_pool_share=0.10)
    inputs = _default_inputs(ass=ass)
    # avail=200M, tvl=500M, cap=500M×0.10=50M < 200M → max_size=50M
    inputs.target_markets = [_market(avail=200_000_000, tvl=500_000_000)]
    result = optimize(inputs)
    direct = next(r for r in result.routes if r.route_type == "direct_borrow")
    assert direct.max_executable_usd == pytest.approx(50_000_000)


def test_direct_borrow_infeasible_when_no_markets():
    """Direct borrow should be infeasible when no lending markets exist."""
    inputs = _default_inputs()
    inputs.target_markets = []
    result = optimize(inputs)
    direct = next(r for r in result.routes if r.route_type == "direct_borrow")
    assert not direct.feasible


def test_direct_borrow_bottleneck_on_high_utilization():
    """High utilization should appear as a bottleneck."""
    inputs = _default_inputs()
    inputs.target_markets = [_market(utilization=0.96, avail=5_000_000)]
    result = optimize(inputs)
    direct = next(r for r in result.routes if r.route_type == "direct_borrow")
    bottleneck_types = [b.constraint for b in direct.bottlenecks]
    assert any("utilization" in b.lower() for b in bottleneck_types)


# ─────────────────────────────────────────────────────────────────────────────
# Stable borrow → spot
# ─────────────────────────────────────────────────────────────────────────────


def test_stable_spot_cost_includes_slippage():
    """Stable→spot cost = stable_borrow_apy×100 + spot_slippage_bps."""
    ass = RouteAssumptions(spot_slippage_bps=15.0)
    result = optimize(_default_inputs(stable_borrow_apy=5.0, ass=ass))
    route = next(r for r in result.routes if r.route_type == "stable_borrow_spot")
    assert route.total_cost_bps == pytest.approx(500.0 + 15.0)


def test_stable_spot_not_applicable_for_stablecoins():
    """Stable→spot should be infeasible when target asset is USDC."""
    result = optimize(_default_inputs(symbol="USDC"))
    route = next(r for r in result.routes if r.route_type == "stable_borrow_spot")
    assert not route.feasible


# ─────────────────────────────────────────────────────────────────────────────
# Wrapper transform
# ─────────────────────────────────────────────────────────────────────────────


def test_wrapper_cost_includes_fee_and_slippage():
    """Wrapper cost = source_borrow_apy×100 + fee + slippage + extra_slippage."""
    ass = RouteAssumptions(wrapper_extra_slippage_bps=5.0)
    source_market = _market(protocol="compound", borrow_apy=4.5)
    transform = _transform(from_asset="ETH", to_asset="stETH", fee_bps=0.0, slippage_bps=2.0)
    inputs = _default_inputs(
        symbol="stETH",
        ass=ass,
        transforms=[transform],
        transform_source_markets={"ETH": [source_market]},
    )
    result = optimize(inputs)
    route = next(r for r in result.routes if r.route_type == "wrapper_transform")
    # 4.5%×100 + 0 + 2 + 5 = 457
    assert route.total_cost_bps == pytest.approx(450.0 + 0.0 + 2.0 + 5.0)


def test_wrapper_unbonding_premium_applied():
    """Unbonding days should add a premium to wrapper cost."""
    ass = RouteAssumptions(unbonding_bps_per_day=2.0, wrapper_extra_slippage_bps=0.0)
    source_market = _market(borrow_apy=4.0)
    transform = _transform(
        from_asset="stETH", to_asset="ETH", fee_bps=0.0, slippage_bps=0.0,
        unbonding_days=1.5,
    )
    inputs = _default_inputs(
        symbol="ETH",
        ass=ass,
        transforms=[transform],
        transform_source_markets={"stETH": [source_market]},
    )
    result = optimize(inputs)
    route = next(r for r in result.routes if r.route_type == "wrapper_transform")
    # 4.0×100 + 0 + 0 + 0 + 1.5×2 = 403
    assert route.total_cost_bps == pytest.approx(400.0 + 1.5 * 2.0)


def test_wrapper_infeasible_when_no_paths():
    """Wrapper should be infeasible when no transforms are defined."""
    result = optimize(_default_inputs(symbol="ETH", transforms=[]))
    route = next(r for r in result.routes if r.route_type == "wrapper_transform")
    assert not route.feasible


def test_wrapper_picks_cheapest_source():
    """With two transform paths, the cheaper source should be selected."""
    ass = RouteAssumptions(wrapper_extra_slippage_bps=0.0)
    cheap = _market(protocol="morpho", borrow_apy=3.0)
    expensive = _market(protocol="aave", borrow_apy=6.0)
    transforms = [
        _transform(from_asset="ETH", to_asset="stETH", fee_bps=0.0, slippage_bps=0.0),
        _transform(from_asset="WETH", to_asset="stETH", fee_bps=0.0, slippage_bps=0.0),
    ]
    inputs = _default_inputs(
        symbol="stETH",
        ass=ass,
        transforms=transforms,
        transform_source_markets={"ETH": [expensive], "WETH": [cheap]},
    )
    result = optimize(inputs)
    route = next(r for r in result.routes if r.route_type == "wrapper_transform")
    assert route.total_cost_bps == pytest.approx(300.0)  # 3.0%×100


# ─────────────────────────────────────────────────────────────────────────────
# Synthetic hedge
# ─────────────────────────────────────────────────────────────────────────────


def test_synthetic_cost_includes_funding():
    """Synthetic cost = stable_borrow + funding_annualized + variance_premium."""
    ass = RouteAssumptions(funding_variance_premium_bps=100.0)
    # 0.0003/8h × 3 × 365 = 32.85% ann → 3285 bps
    result = optimize(_default_inputs(
        stable_borrow_apy=5.0, funding_rate=0.0003, ass=ass
    ))
    route = next(r for r in result.routes if r.route_type == "synthetic_hedge")
    # 5%×100 + 32.85%×100 + 100 = 500 + 3285 + 100 = 3885
    import pytest as pt
    assert route.total_cost_bps == pt.approx(500.0 + 0.0003 * 3 * 365 * 100 * 100 + 100.0, abs=1.0)


def test_synthetic_negative_funding_reduces_cost():
    """Negative funding income reduces synthetic route cost."""
    ass = RouteAssumptions(funding_variance_premium_bps=50.0)
    result = optimize(_default_inputs(
        stable_borrow_apy=5.0, funding_rate=-0.0001, ass=ass
    ))
    route = next(r for r in result.routes if r.route_type == "synthetic_hedge")
    stable_comp = next(c for c in route.cost_components if c.name == "stablecoin_borrow_apy")
    fund_comp = next(c for c in route.cost_components if c.name == "perpetual_funding")
    assert fund_comp.value_bps < 0


def test_synthetic_infeasible_for_stablecoin():
    result = optimize(_default_inputs(symbol="USDC", funding_rate=0.0002))
    route = next(r for r in result.routes if r.route_type == "synthetic_hedge")
    assert not route.feasible


def test_synthetic_oi_cap_limits_max_size():
    """max_executable_usd is capped at max_oi_share × total OI."""
    ass = RouteAssumptions(max_oi_share=0.05, funding_variance_premium_bps=0.0)
    inputs = _default_inputs(stable_borrow_apy=5.0, funding_rate=0.0002, ass=ass)
    inputs.open_interest_usd = 100_000_000   # 100M OI
    result = optimize(inputs)
    route = next(r for r in result.routes if r.route_type == "synthetic_hedge")
    assert route.max_executable_usd == pytest.approx(5_000_000)  # 5% of 100M


# ─────────────────────────────────────────────────────────────────────────────
# Ranking
# ─────────────────────────────────────────────────────────────────────────────


def test_cheapest_route_is_rank_one():
    """The route with lowest effective cost should be rank 1."""
    result = optimize(_default_inputs(target_borrow_apy=2.0, stable_borrow_apy=8.0))
    assert result.routes[0].rank == 1
    effective_costs = [r.effective_cost_bps for r in result.routes]
    assert effective_costs == sorted(effective_costs)


def test_size_shortfall_increases_effective_cost():
    """A route that can only fill half the request gets a size penalty."""
    ass = RouteAssumptions(size_shortfall_penalty_bps=200.0)
    # Make direct market very small (tiny avail, small TVL)
    inputs = _default_inputs(target_borrow_apy=1.0, stable_borrow_apy=5.0, ass=ass, size=20_000_000)
    inputs.target_markets = [_market(borrow_apy=1.0, avail=5_000_000, tvl=10_000_000)]
    result = optimize(inputs)
    direct = next(r for r in result.routes if r.route_type == "direct_borrow")
    # shortfall = (20M - 2.5M) / 20M = 0.875 → penalty = 200 × 0.875 = 175
    expected_penalty = ((20_000_000 - direct.max_executable_usd) / 20_000_000) * 200.0
    assert direct.effective_cost_bps == pytest.approx(
        direct.total_cost_bps + expected_penalty, abs=1.0
    )


def test_infeasible_routes_are_ranked_last():
    """Infeasible routes should always have higher rank number than feasible ones."""
    result = optimize(_default_inputs(symbol="USDC"))  # synthetic + stable→spot = infeasible
    feasible = [r for r in result.routes if r.feasible]
    infeasible = [r for r in result.routes if not r.feasible]
    if feasible and infeasible:
        assert max(r.rank for r in feasible) < min(r.rank for r in infeasible)


def test_routes_are_sorted_by_rank():
    result = optimize(_default_inputs())
    ranks = [r.rank for r in result.routes]
    assert ranks == sorted(ranks)


# ─────────────────────────────────────────────────────────────────────────────
# Summary and recommended_route
# ─────────────────────────────────────────────────────────────────────────────


def test_recommended_route_matches_rank_one():
    result = optimize(_default_inputs())
    rank_one = result.routes[0]
    assert result.recommended_route == rank_one.route_type


def test_summary_mentions_asset_and_cost():
    result = optimize(_default_inputs(symbol="BTC", target_borrow_apy=3.0))
    assert "BTC" in result.summary
    assert "bps" in result.summary.lower()


def test_summary_no_data_is_graceful():
    """Empty input data should still produce a summary."""
    inputs = RouteOptimizerInputs(
        target_asset="XYZ",
        request_size_usd=1_000_000,
    )
    result = optimize(inputs)
    assert "XYZ" in result.summary
    assert len(result.summary) > 10


# ─────────────────────────────────────────────────────────────────────────────
# Assumptions override
# ─────────────────────────────────────────────────────────────────────────────


def test_max_pool_share_override_reduces_capacity():
    ass_tight = RouteAssumptions(max_pool_share=0.05)
    ass_loose = RouteAssumptions(max_pool_share=0.50)
    inputs_tight = _default_inputs(ass=ass_tight)
    inputs_loose = _default_inputs(ass=ass_loose)
    r_tight = optimize(inputs_tight)
    r_loose = optimize(inputs_loose)
    direct_tight = next(r for r in r_tight.routes if r.route_type == "direct_borrow")
    direct_loose = next(r for r in r_loose.routes if r.route_type == "direct_borrow")
    assert direct_tight.max_executable_usd <= direct_loose.max_executable_usd


def test_all_assumptions_present_in_result():
    result = optimize(_default_inputs())
    ass = result.assumptions
    assert ass.max_pool_share > 0
    assert ass.spot_slippage_bps > 0
    assert ass.funding_variance_premium_bps > 0


import pytest  # noqa: E402  (intentional late import for clarity in test above)
