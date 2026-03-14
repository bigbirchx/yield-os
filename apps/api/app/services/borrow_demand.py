"""
Borrow-demand explanation engine.

Design principles:
- Pure function — `analyze(inputs)` contains zero I/O.
- Every output sentence is traceable to a named metric and source.
- Scoring is rule-based and deterministic (no ML, no LLM).
- Confidence reflects data coverage and freshness, not model uncertainty.

Factor taxonomy
───────────────
  perpetual_funding  — elevated funding ↔ demand to borrow the spot asset
  futures_basis      — wide basis ↔ cash-and-carry incentive
  oi_momentum        — rising OI + elevated funding = reinforcing conviction
  lending_rate       — borrow APY vs 30d median (market-clearing signal)
  utilization        — high util = supply cannot meet demand
  cap_headroom       — low available liquidity = hard capacity constraint
  staking_premium    — staking APY > borrow APY → leveraged staking trade economics

Demand score
────────────
  demand_score = Σ(weight_i × score_i × sign_i)
  sign = +1 for "elevates", −1 for "suppresses"
  demand_level:
    > 0.30  → ELEVATED
    < -0.20 → SUPPRESSED
    else    → NORMAL

Confidence score
────────────────
  coverage_ratio = n_factors_with_data / N_EXPECTED_FACTORS
  freshness_weight per factor:
    < 15 min  → 1.00
    < 1 h     → 0.85
    < 4 h     → 0.65
    otherwise → 0.35
  confidence = clamp(0.40 × coverage_ratio + 0.60 × mean_freshness_weight, 0.10, 0.97)
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal


# ─────────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class HistoryPoint:
    timestamp: datetime
    value: float


@dataclass
class LendingMarketInput:
    protocol: str
    market: str
    chain: str | None
    borrow_apy: float | None
    utilization: float | None
    available_liquidity_usd: float | None
    tvl_usd: float | None
    snapshot_at: datetime | None


@dataclass
class StakingInput:
    symbol: str
    protocol: str
    staking_apy: float | None
    tvl_usd: float | None


@dataclass
class EventOverlay:
    """Manual annotation — e.g. token unlock, governance vote, known catalyst."""

    label: str
    event_date: datetime
    impact: Literal["elevates", "suppresses", "neutral"]
    source: str
    notes: str = ""


@dataclass
class TransformMetadata:
    """Describes available conversion paths and their economics."""

    from_asset: str
    to_asset: str
    transform_type: str  # "stake" | "unstake" | "wrap" | "unwrap" | "bridge"
    fee_bps: float | None
    latency_seconds: float | None
    unbonding_days: float | None


@dataclass
class BorrowDemandInputs:
    symbol: str

    # Current derivatives snapshot
    funding_rate: float | None = None          # raw 8h rate (e.g. 0.0001)
    basis_annualized: float | None = None      # decimal (0.10 = 10%)
    open_interest_usd: float | None = None

    # Historical series — sorted ascending by timestamp
    funding_history: list[HistoryPoint] = field(default_factory=list)
    oi_history: list[HistoryPoint] = field(default_factory=list)
    basis_history: list[HistoryPoint] = field(default_factory=list)

    # Lending markets
    lending_markets: list[LendingMarketInput] = field(default_factory=list)
    borrow_rate_history: list[HistoryPoint] = field(default_factory=list)  # max/avg borrow APY

    # Staking
    staking: list[StakingInput] = field(default_factory=list)

    # Event overlays (manual)
    event_overlays: list[EventOverlay] = field(default_factory=list)

    # Transform paths
    transforms: list[TransformMetadata] = field(default_factory=list)

    # Data window actually used
    data_window_days: int = 30


# ─────────────────────────────────────────────────────────────────────────────
# Output structures
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ReasonFactor:
    name: str
    display_label: str
    direction: Literal["elevates", "suppresses", "neutral"]
    score: float          # 0.0–1.0 magnitude of the effect
    value: float | None   # measured value in natural display units
    baseline: float | None  # historical context value (e.g. 30d median)
    value_unit: str       # e.g. "% ann." or "%" or "$M"
    metric_source: str    # e.g. "Velo" or "DeFiLlama"
    metric_name: str      # snake_case field name from data dictionary
    snapshot_at: datetime | None
    evidence_note: str    # 1-sentence factual note used in explanation


@dataclass
class BorrowDemandAnalysis:
    symbol: str
    demand_level: Literal["elevated", "normal", "suppressed"]
    demand_score: float          # raw weighted sum before clamping
    confidence: float            # 0.0–1.0
    reasons: list[ReasonFactor]  # sorted by |score| desc
    explanation: str             # 3–5 sentences, traceable to metrics
    computed_at: datetime
    data_window_days: int
    event_overlays: list[EventOverlay]


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

_WEIGHTS: dict[str, float] = {
    "perpetual_funding": 0.25,
    "futures_basis":     0.20,
    "oi_momentum":       0.15,
    "lending_rate":      0.20,
    "utilization":       0.15,
    "cap_headroom":      0.10,
    "staking_premium":   0.10,
}

N_EXPECTED_FACTORS = len(_WEIGHTS)


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _freshness_weight(ts: datetime | None) -> float:
    if ts is None:
        return 0.0
    age = datetime.now(UTC) - ts
    if age < timedelta(minutes=15):
        return 1.00
    if age < timedelta(hours=1):
        return 0.85
    if age < timedelta(hours=4):
        return 0.65
    return 0.35


# ─────────────────────────────────────────────────────────────────────────────
# Factor scorers — each returns a ReasonFactor or None
# ─────────────────────────────────────────────────────────────────────────────


def _score_funding(
    current: float | None,
    history: list[HistoryPoint],
    snapshot_at: datetime | None,
) -> ReasonFactor | None:
    if current is None:
        return None

    ann = current * 3 * 365 * 100  # annualized percentage
    history_vals = [p.value * 3 * 365 * 100 for p in history]
    baseline = _median(history_vals) if history_vals else 8.0

    if ann >= 0:
        # Score peaks at 1.0 when funding reaches 25% annualized
        score = _clamp((ann - 5.0) / 20.0)
        direction: Literal["elevates", "suppresses", "neutral"] = (
            "elevates" if ann > 8.0 else "neutral"
        )
        note = (
            f"Perpetual funding is {ann:.2f}% ann. "
            f"({'above' if ann > baseline else 'in line with'} the "
            f"{len(history_vals)}-period median of {baseline:.2f}% ann.)"
        )
    else:
        score = _clamp((-ann - 2.0) / 15.0)
        direction = "suppresses"
        note = (
            f"Perpetual funding is negative at {ann:.2f}% ann., "
            "indicating net short positioning and reduced borrow demand."
        )

    return ReasonFactor(
        name="perpetual_funding",
        display_label="Perpetual Funding Rate",
        direction=direction,
        score=score,
        value=round(ann, 4),
        baseline=round(baseline, 4) if baseline is not None else None,
        value_unit="% ann.",
        metric_source="Velo",
        metric_name="funding_rate",
        snapshot_at=snapshot_at,
        evidence_note=note,
    )


def _score_basis(
    current: float | None,
    history: list[HistoryPoint],
    snapshot_at: datetime | None,
) -> ReasonFactor | None:
    if current is None:
        return None

    pct = current * 100  # as percentage
    history_vals = [p.value * 100 for p in history]
    baseline = _median(history_vals) if history_vals else 5.0

    if pct >= 0:
        # Score peaks at 1.0 when basis reaches 20% annualized
        score = _clamp((pct - 3.0) / 17.0)
        direction = "elevates" if pct > 6.0 else "neutral"
        note = (
            f"The annualized futures basis is {pct:.2f}%, "
            f"{'above' if pct > baseline else 'near'} the "
            f"{len(history_vals)}-period median of {baseline:.2f}%. "
            "A wide basis incentivizes cash-and-carry borrowing."
        )
    else:
        score = _clamp(-pct / 10.0)
        direction = "suppresses"
        note = (
            f"The futures basis is negative ({pct:.2f}% ann.), "
            "removing the cash-and-carry incentive."
        )

    return ReasonFactor(
        name="futures_basis",
        display_label="Futures Basis",
        direction=direction,
        score=score,
        value=round(pct, 4),
        baseline=round(baseline, 4) if baseline is not None else None,
        value_unit="% ann.",
        metric_source="Velo",
        metric_name="basis_annualized",
        snapshot_at=snapshot_at,
        evidence_note=note,
    )


def _score_oi_momentum(
    current_oi: float | None,
    history: list[HistoryPoint],
    snapshot_at: datetime | None,
) -> ReasonFactor | None:
    if current_oi is None or not history:
        return None

    oi_vals = [p.value for p in history]
    median_oi = _median(oi_vals)
    if not median_oi or median_oi == 0:
        return None

    pct_change = (current_oi - median_oi) / median_oi * 100
    score = _clamp(pct_change / 40.0)  # 1.0 at +40% above median

    if pct_change > 10:
        direction = "elevates"
        note = (
            f"Open interest is ${current_oi / 1e6:.0f}M, "
            f"+{pct_change:.1f}% above the {len(oi_vals)}-period median "
            f"of ${median_oi / 1e6:.0f}M, reinforcing the demand signal."
        )
    elif pct_change < -10:
        direction = "suppresses"
        score = _clamp(-pct_change / 40.0)
        note = (
            f"Open interest has declined {-pct_change:.1f}% below its "
            f"{len(oi_vals)}-period median, suggesting deleveraging."
        )
    else:
        direction = "neutral"
        score = 0.0
        note = (
            f"Open interest is ${current_oi / 1e6:.0f}M, "
            f"near the {len(oi_vals)}-period median of ${median_oi / 1e6:.0f}M."
        )

    return ReasonFactor(
        name="oi_momentum",
        display_label="Open Interest Momentum",
        direction=direction,
        score=score,
        value=round(current_oi / 1e6, 2),
        baseline=round(median_oi / 1e6, 2),
        value_unit="$M OI",
        metric_source="Velo",
        metric_name="open_interest_usd",
        snapshot_at=snapshot_at,
        evidence_note=note,
    )


def _score_lending_rate(
    markets: list[LendingMarketInput],
    history: list[HistoryPoint],
) -> ReasonFactor | None:
    rates = [m.borrow_apy for m in markets if m.borrow_apy is not None]
    if not rates:
        return None

    current = max(rates)
    best_market = next(
        (m for m in markets if m.borrow_apy == current), None
    )
    history_vals = [p.value for p in history]
    baseline = _median(history_vals) if history_vals else 5.0
    snapshot_at = best_market.snapshot_at if best_market else None

    if baseline and baseline > 0:
        z = (current - baseline) / baseline
        score = _clamp(z / 0.5)  # 1.0 when rate is 50% above its median
    else:
        score = _clamp((current - 5.0) / 15.0)

    direction = "elevates" if current > max(baseline or 0, 6.0) else "neutral"
    note = (
        f"The highest borrow rate is {current:.2f}% on "
        f"{best_market.protocol if best_market else 'a lending protocol'}"
        + (f" ({best_market.chain})" if best_market and best_market.chain else "")
        + f", vs a {len(history_vals)}-period median of {baseline:.2f}%."
    )

    return ReasonFactor(
        name="lending_rate",
        display_label="Lending Market Borrow Rate",
        direction=direction,
        score=score,
        value=round(current, 4),
        baseline=round(baseline, 4) if baseline is not None else None,
        value_unit="% APY",
        metric_source="DeFiLlama",
        metric_name="borrow_apy",
        snapshot_at=snapshot_at,
        evidence_note=note,
    )


def _score_utilization(
    markets: list[LendingMarketInput],
) -> ReasonFactor | None:
    utils = [m.utilization for m in markets if m.utilization is not None]
    if not utils:
        return None

    peak = max(utils)
    best_market = next((m for m in markets if m.utilization == peak), None)
    snapshot_at = best_market.snapshot_at if best_market else None

    # Score peaks at 1.0 when utilization = 97%
    score = _clamp((peak - 0.50) / 0.47)
    direction = "elevates" if peak > 0.80 else "neutral"

    note = (
        f"Peak utilization across markets is {peak * 100:.1f}% "
        f"({'on ' + best_market.protocol if best_market else ''}). "
        + (
            "Near-full utilization indicates supply is fully absorbed."
            if peak > 0.90
            else "Utilization is elevated relative to a typical 50–80% range."
            if peak > 0.80
            else "Utilization is within normal operating range."
        )
    )

    return ReasonFactor(
        name="utilization",
        display_label="Market Utilization",
        direction=direction,
        score=score,
        value=round(peak * 100, 2),
        baseline=70.0,  # rule-of-thumb neutral midpoint
        value_unit="%",
        metric_source="DeFiLlama",
        metric_name="utilization",
        snapshot_at=snapshot_at,
        evidence_note=note,
    )


def _score_cap_headroom(
    markets: list[LendingMarketInput],
) -> ReasonFactor | None:
    valid = [m for m in markets if m.available_liquidity_usd is not None and m.tvl_usd]
    if not valid:
        return None

    total_avail = sum(m.available_liquidity_usd for m in valid)  # type: ignore[arg-type]
    total_supply = sum(m.tvl_usd for m in valid)  # type: ignore[arg-type]

    if total_supply == 0:
        return None

    headroom_ratio = total_avail / total_supply
    # Score peaks at 1.0 when headroom drops to 5%
    score = _clamp((0.20 - headroom_ratio) / 0.15)
    direction = "elevates" if headroom_ratio < 0.15 else "neutral"

    total_avail_m = total_avail / 1e6
    note = (
        f"Aggregate available capacity across tracked markets is "
        f"${total_avail_m:.1f}M ({headroom_ratio * 100:.1f}% of total supply). "
        + (
            "Capacity is severely constrained."
            if headroom_ratio < 0.10
            else "Capacity is tightening."
            if headroom_ratio < 0.20
            else "Capacity is adequate."
        )
    )

    return ReasonFactor(
        name="cap_headroom",
        display_label="Capacity Headroom",
        direction=direction,
        score=score,
        value=round(headroom_ratio * 100, 2),
        baseline=20.0,  # 20% headroom = neutral
        value_unit="% of supply",
        metric_source="DeFiLlama",
        metric_name="available_liquidity_usd",
        snapshot_at=None,
        evidence_note=note,
    )


def _score_staking_premium(
    staking: list[StakingInput],
    markets: list[LendingMarketInput],
) -> ReasonFactor | None:
    staking_rates = [s.staking_apy for s in staking if s.staking_apy is not None]
    borrow_rates = [m.borrow_apy for m in markets if m.borrow_apy is not None]
    if not staking_rates or not borrow_rates:
        return None

    best_stake = max(staking_rates)
    best_borrow = max(borrow_rates)
    best_staker = next((s for s in staking if s.staking_apy == best_stake), None)

    spread = best_stake - best_borrow
    if spread <= 0:
        # Staking yield does not exceed borrow cost → no leveraged staking incentive
        return ReasonFactor(
            name="staking_premium",
            display_label="Staking Yield Premium",
            direction="neutral",
            score=0.0,
            value=round(spread, 4),
            baseline=0.0,
            value_unit="% spread",
            metric_source="DeFiLlama",
            metric_name="staking_apy",
            snapshot_at=None,
            evidence_note=(
                f"Staking yield ({best_stake:.2f}%) does not exceed the borrow rate "
                f"({best_borrow:.2f}%), so leveraged staking is not economically attractive."
            ),
        )

    # Positive spread → leveraged staking is viable → adds to borrow demand
    score = _clamp(spread / 10.0)  # 1.0 at a 10pp spread
    note = (
        f"The best staking yield ({best_staker.symbol if best_staker else 'staking'} "
        f"via {best_staker.protocol if best_staker else 'protocol'}) is {best_stake:.2f}% "
        f"vs a borrow rate of {best_borrow:.2f}%, a +{spread:.2f}pp carry that "
        "incentivizes leveraged staking strategies."
    )

    return ReasonFactor(
        name="staking_premium",
        display_label="Staking Yield Premium",
        direction="elevates",
        score=score,
        value=round(spread, 4),
        baseline=0.0,
        value_unit="% spread",
        metric_source="DeFiLlama",
        metric_name="staking_apy",
        snapshot_at=None,
        evidence_note=note,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Explanation builder
# ─────────────────────────────────────────────────────────────────────────────

_DEMAND_OPENER: dict[str, str] = {
    "elevated": "{symbol} borrow demand is elevated.",
    "normal":   "{symbol} borrow demand is within normal bounds.",
    "suppressed": "{symbol} borrow demand appears suppressed.",
}


def _build_explanation(
    symbol: str,
    demand_level: Literal["elevated", "normal", "suppressed"],
    factors: list[ReasonFactor],
    event_overlays: list[EventOverlay],
    confidence: float,
) -> str:
    sentences: list[str] = [_DEMAND_OPENER[demand_level].format(symbol=symbol)]

    # Add top 2-3 factor sentences (only factors with evidence content)
    scored = sorted(
        [f for f in factors if f.evidence_note and f.score > 0.05],
        key=lambda f: f.score,
        reverse=True,
    )
    for f in scored[:3]:
        sentences.append(f.evidence_note)

    # Event overlay note
    recent_events = [
        e for e in event_overlays
        if abs((e.event_date - datetime.now(UTC)).days) <= 14
    ]
    if recent_events:
        ev = recent_events[0]
        sentences.append(
            f"Note: {ev.label} ({ev.source})"
            + (f" — {ev.notes}" if ev.notes else ".")
        )

    # Confidence qualifier at end if low
    if confidence < 0.45:
        sentences.append(
            "Data coverage is limited; these signals should be treated as indicative."
        )

    return " ".join(sentences)


# ─────────────────────────────────────────────────────────────────────────────
# Main engine
# ─────────────────────────────────────────────────────────────────────────────


def analyze(inputs: BorrowDemandInputs) -> BorrowDemandAnalysis:
    """
    Pure function. Takes BorrowDemandInputs, returns BorrowDemandAnalysis.
    No database or network calls.
    """
    now = datetime.now(UTC)
    latest_deriv_ts = inputs.funding_history[-1].timestamp if inputs.funding_history else None

    # ── Compute factors ──────────────────────────────────────────────────────
    raw_factors: list[ReasonFactor | None] = [
        _score_funding(inputs.funding_rate, inputs.funding_history, latest_deriv_ts),
        _score_basis(inputs.basis_annualized, inputs.basis_history, latest_deriv_ts),
        _score_oi_momentum(inputs.open_interest_usd, inputs.oi_history, latest_deriv_ts),
        _score_lending_rate(inputs.lending_markets, inputs.borrow_rate_history),
        _score_utilization(inputs.lending_markets),
        _score_cap_headroom(inputs.lending_markets),
        _score_staking_premium(inputs.staking, inputs.lending_markets),
    ]
    factors: list[ReasonFactor] = [f for f in raw_factors if f is not None]

    # ── Demand score ─────────────────────────────────────────────────────────
    demand_score = 0.0
    for f in factors:
        w = _WEIGHTS.get(f.name, 0.10)
        sign = -1 if f.direction == "suppresses" else 1
        demand_score += w * f.score * sign

    # A single strongly-firing factor (e.g. funding at score=1.0, weight=0.25)
    # contributes 0.25. Threshold set to 0.20 so one dominant factor is
    # sufficient to call "elevated"; two moderate factors are also enough.
    if demand_score > 0.20:
        demand_level: Literal["elevated", "normal", "suppressed"] = "elevated"
    elif demand_score < -0.15:
        demand_level = "suppressed"
    else:
        demand_level = "normal"

    # ── Confidence ───────────────────────────────────────────────────────────
    coverage_ratio = len(factors) / N_EXPECTED_FACTORS

    freshness_weights: list[float] = []
    if latest_deriv_ts:
        freshness_weights.append(_freshness_weight(latest_deriv_ts))
    for m in inputs.lending_markets:
        freshness_weights.append(_freshness_weight(m.snapshot_at))
    mean_freshness = (
        sum(freshness_weights) / len(freshness_weights) if freshness_weights else 0.5
    )

    confidence = _clamp(0.40 * coverage_ratio + 0.60 * mean_freshness, 0.10, 0.97)

    # ── Sort factors by absolute impact ──────────────────────────────────────
    factors_sorted = sorted(factors, key=lambda f: f.score, reverse=True)

    # ── Explanation ──────────────────────────────────────────────────────────
    explanation = _build_explanation(
        inputs.symbol, demand_level, factors_sorted,
        inputs.event_overlays, confidence,
    )

    return BorrowDemandAnalysis(
        symbol=inputs.symbol,
        demand_level=demand_level,
        demand_score=round(demand_score, 4),
        confidence=round(confidence, 4),
        reasons=factors_sorted,
        explanation=explanation,
        computed_at=now,
        data_window_days=inputs.data_window_days,
        event_overlays=inputs.event_overlays,
    )
