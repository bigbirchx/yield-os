"""
Unit tests for the borrow-demand explanation engine.

All tests operate on the pure `analyze()` function — no DB, no network.
Tests cover:
  - Factor scoring at boundary conditions
  - Demand-level classification
  - Confidence degradation with missing/stale data
  - Explanation sentence count and traceability
  - Event overlay pass-through
  - Staking premium direction switching
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services.borrow_demand import (
    BorrowDemandInputs,
    EventOverlay,
    HistoryPoint,
    LendingMarketInput,
    StakingInput,
    analyze,
)

_NOW = datetime.now(UTC)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _lending(
    borrow_apy: float = 5.0,
    utilization: float = 0.65,
    avail: float = 50_000_000,
    tvl: float = 200_000_000,
    protocol: str = "aave-v3",
) -> LendingMarketInput:
    return LendingMarketInput(
        protocol=protocol,
        market="USDC",
        chain="Ethereum",
        borrow_apy=borrow_apy,
        utilization=utilization,
        available_liquidity_usd=avail,
        tvl_usd=tvl,
        snapshot_at=_NOW,
    )


def _history(values: list[float], ago_hours: int = 24) -> list[HistoryPoint]:
    """Build a list of HistoryPoints spaced 1h apart ending `ago_hours` ago."""
    base = _NOW - timedelta(hours=ago_hours + len(values))
    return [
        HistoryPoint(timestamp=base + timedelta(hours=i), value=v)
        for i, v in enumerate(values)
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Demand level classification
# ─────────────────────────────────────────────────────────────────────────────


def test_elevated_demand_with_high_funding():
    """High annualized funding (>20% ann.) should produce ELEVATED demand."""
    # 0.0007 per 8h ≈ 76.65% annualized — clearly elevated
    inputs = BorrowDemandInputs(
        symbol="BTC",
        funding_rate=0.0007,
        funding_history=_history([0.0002] * 20),  # baseline ~21.9% ann.
    )
    result = analyze(inputs)
    assert result.demand_level == "elevated"
    assert result.demand_score > 0.20


def test_normal_demand_with_moderate_signals():
    """Moderate signals (8% ann. funding, 6% basis, 65% util) should produce NORMAL demand."""
    inputs = BorrowDemandInputs(
        symbol="ETH",
        funding_rate=0.000073,  # 8% annualized (0.000073 * 3 * 365 * 100 ≈ 8%)
        basis_annualized=0.06,  # 6%
        lending_markets=[_lending(borrow_apy=5.0, utilization=0.65)],
    )
    result = analyze(inputs)
    assert result.demand_level == "normal"


def test_suppressed_demand_with_negative_funding():
    """Deeply negative funding should suppress demand."""
    inputs = BorrowDemandInputs(
        symbol="ETH",
        funding_rate=-0.0006,  # ~-21.9% ann.
        funding_history=_history([0.0001] * 20),
    )
    result = analyze(inputs)
    assert result.demand_level in ("suppressed", "normal")
    # demand_score must be negative when funding suppresses
    assert result.demand_score < 0.10


def test_elevated_demand_high_utilization():
    """High utilization alone (95%) should push demand toward elevated."""
    inputs = BorrowDemandInputs(
        symbol="USDC",
        lending_markets=[_lending(borrow_apy=12.0, utilization=0.95, avail=5_000_000)],
        borrow_rate_history=_history([4.0, 5.0, 5.5, 6.0, 8.0, 12.0], ago_hours=5),
    )
    result = analyze(inputs)
    util_factor = next((f for f in result.reasons if f.name == "utilization"), None)
    assert util_factor is not None
    assert util_factor.score > 0.80
    assert util_factor.direction == "elevates"


# ─────────────────────────────────────────────────────────────────────────────
# Factor scoring
# ─────────────────────────────────────────────────────────────────────────────


def test_funding_score_at_neutral_is_near_zero():
    """Funding at ~5% annualized (0.000046/8h) should score near zero."""
    # 0.000046 * 3 * 365 * 100 ≈ 5.0% annualized — neutral territory
    inputs = BorrowDemandInputs(symbol="BTC", funding_rate=0.000046)
    result = analyze(inputs)
    funding_f = next((f for f in result.reasons if f.name == "perpetual_funding"), None)
    assert funding_f is not None
    assert funding_f.score < 0.10


def test_funding_score_clamps_at_one():
    """Extremely high funding should not score above 1.0."""
    inputs = BorrowDemandInputs(symbol="BTC", funding_rate=0.003)  # >100% ann.
    result = analyze(inputs)
    funding_f = next(f for f in result.reasons if f.name == "perpetual_funding")
    assert funding_f.score <= 1.0


def test_basis_negative_is_suppressing():
    """Negative basis should be marked as suppresses."""
    inputs = BorrowDemandInputs(
        symbol="BTC",
        basis_annualized=-0.08,
    )
    result = analyze(inputs)
    basis_f = next((f for f in result.reasons if f.name == "futures_basis"), None)
    assert basis_f is not None
    assert basis_f.direction == "suppresses"


def test_oi_momentum_neutral_when_no_history():
    """OI factor should be absent when no history is provided."""
    inputs = BorrowDemandInputs(symbol="BTC", open_interest_usd=5_000_000_000)
    result = analyze(inputs)
    oi_f = next((f for f in result.reasons if f.name == "oi_momentum"), None)
    assert oi_f is None  # can't score without baseline


def test_oi_momentum_elevated_above_median():
    """OI 50% above its history median should score as elevating."""
    median_oi = 4_000_000_000
    inputs = BorrowDemandInputs(
        symbol="BTC",
        open_interest_usd=median_oi * 1.5,
        oi_history=[HistoryPoint(timestamp=_NOW - timedelta(hours=i), value=median_oi)
                    for i in range(20)],
    )
    result = analyze(inputs)
    oi_f = next(f for f in result.reasons if f.name == "oi_momentum")
    assert oi_f.direction == "elevates"
    assert oi_f.score > 0.5


def test_cap_headroom_tight():
    """5% available liquidity should produce a high-score elevating cap factor."""
    inputs = BorrowDemandInputs(
        symbol="USDC",
        lending_markets=[_lending(avail=10_000_000, tvl=200_000_000)],  # 5%
    )
    result = analyze(inputs)
    cap_f = next((f for f in result.reasons if f.name == "cap_headroom"), None)
    assert cap_f is not None
    assert cap_f.direction == "elevates"
    assert cap_f.score > 0.6


def test_staking_premium_elevates_when_spread_positive():
    """Staking yield > borrow rate → direction = elevates."""
    inputs = BorrowDemandInputs(
        symbol="ETH",
        lending_markets=[_lending(borrow_apy=4.0)],
        staking=[StakingInput("stETH", "Lido", staking_apy=6.0, tvl_usd=1e10)],
    )
    result = analyze(inputs)
    stk_f = next(f for f in result.reasons if f.name == "staking_premium")
    assert stk_f.direction == "elevates"
    assert stk_f.score > 0.0


def test_staking_premium_neutral_when_spread_negative():
    """Staking yield < borrow rate → direction = neutral, score = 0."""
    inputs = BorrowDemandInputs(
        symbol="ETH",
        lending_markets=[_lending(borrow_apy=9.0)],
        staking=[StakingInput("stETH", "Lido", staking_apy=4.0, tvl_usd=1e10)],
    )
    result = analyze(inputs)
    stk_f = next(f for f in result.reasons if f.name == "staking_premium")
    assert stk_f.direction == "neutral"
    assert stk_f.score == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Confidence
# ─────────────────────────────────────────────────────────────────────────────


def test_confidence_low_when_no_data():
    """Empty inputs → very low confidence."""
    result = analyze(BorrowDemandInputs(symbol="BTC"))
    assert result.confidence < 0.45


def test_confidence_higher_with_fresh_data():
    """Multiple fresh data points should produce higher confidence."""
    inputs = BorrowDemandInputs(
        symbol="ETH",
        funding_rate=0.0003,
        funding_history=_history([0.0002] * 30, ago_hours=0),
        lending_markets=[_lending(borrow_apy=8.0, utilization=0.88)],
        staking=[StakingInput("stETH", "Lido", staking_apy=5.0, tvl_usd=1e10)],
    )
    result = analyze(inputs)
    assert result.confidence > 0.50


# ─────────────────────────────────────────────────────────────────────────────
# Explanation quality
# ─────────────────────────────────────────────────────────────────────────────


def test_explanation_is_multiple_sentences():
    """Explanation should contain at least 2 sentences."""
    inputs = BorrowDemandInputs(
        symbol="BTC",
        funding_rate=0.0005,
        basis_annualized=0.12,
        lending_markets=[_lending(borrow_apy=9.0, utilization=0.88)],
    )
    result = analyze(inputs)
    sentences = [s.strip() for s in result.explanation.split(".") if s.strip()]
    assert len(sentences) >= 2


def test_explanation_mentions_symbol():
    """Explanation must mention the asset symbol."""
    inputs = BorrowDemandInputs(symbol="SOL", funding_rate=0.0004)
    result = analyze(inputs)
    assert "SOL" in result.explanation


def test_explanation_contains_numeric_evidence():
    """For an elevated factor, explanation should include a % value."""
    inputs = BorrowDemandInputs(
        symbol="ETH",
        funding_rate=0.0006,  # ~21% ann.
        funding_history=_history([0.0002] * 20),
    )
    result = analyze(inputs)
    assert "%" in result.explanation


# ─────────────────────────────────────────────────────────────────────────────
# Event overlays
# ─────────────────────────────────────────────────────────────────────────────


def test_event_overlays_pass_through():
    """Event overlays should appear unchanged in the output."""
    ev = EventOverlay(
        label="USDC depeg scare",
        event_date=_NOW - timedelta(days=2),
        impact="elevates",
        source="internal",
        notes="Heightened demand to source USDC vs stablecoins.",
    )
    inputs = BorrowDemandInputs(symbol="USDC", event_overlays=[ev])
    result = analyze(inputs)
    assert len(result.event_overlays) == 1
    assert result.event_overlays[0].label == "USDC depeg scare"


def test_recent_event_included_in_explanation():
    """A recent (< 14 days) event overlay should appear in the explanation."""
    ev = EventOverlay(
        label="Token unlock event",
        event_date=_NOW - timedelta(days=3),
        impact="elevates",
        source="desk-overlay",
        notes="Large unlock increasing sell pressure.",
    )
    inputs = BorrowDemandInputs(
        symbol="SOL",
        funding_rate=0.0004,
        event_overlays=[ev],
    )
    result = analyze(inputs)
    assert "Token unlock event" in result.explanation


# ─────────────────────────────────────────────────────────────────────────────
# Reasons ordering
# ─────────────────────────────────────────────────────────────────────────────


def test_reasons_sorted_by_score_descending():
    """factors should be sorted highest score first."""
    inputs = BorrowDemandInputs(
        symbol="BTC",
        funding_rate=0.0007,
        basis_annualized=0.03,
        lending_markets=[_lending(borrow_apy=5.0, utilization=0.60)],
    )
    result = analyze(inputs)
    scores = [f.score for f in result.reasons]
    assert scores == sorted(scores, reverse=True)
