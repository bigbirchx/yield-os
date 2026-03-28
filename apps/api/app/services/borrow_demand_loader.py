"""
Input-gathering layer for the borrow-demand engine.

Reads from existing DB repositories and assembles a BorrowDemandInputs
struct, which is then passed to the pure `analyze()` function.

Event overlays are stored in-memory for MVP — keyed by uppercase symbol.
Replace with a DB-backed table when the desk needs persistent annotations.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.snapshot import DerivativesSnapshot, LendingMarketSnapshot
from app.models.staking import StakingSnapshot
from app.repositories.derivatives import get_history as deriv_history
from app.repositories.derivatives import get_latest_per_venue
from app.repositories.lending import get_history as lending_history
from app.repositories.lending import get_latest_per_market
from app.services.borrow_demand import (
    BorrowDemandInputs,
    EventOverlay,
    HistoryPoint,
    LendingMarketInput,
    StakingInput,
    TransformMetadata,
)
from sqlalchemy import func, select

# ─────────────────────────────────────────────────────────────────────────────
# Static event overlays — factual, desk-maintained
# ─────────────────────────────────────────────────────────────────────────────

_EVENT_OVERLAYS: dict[str, list[EventOverlay]] = {
    "ETH": [
        EventOverlay(
            label="Ethereum withdrawals enabled",
            event_date=datetime(2023, 4, 12, tzinfo=UTC),
            impact="suppresses",
            source="Ethereum protocol",
            notes="Shapella enabled withdrawals, reducing LST discount and borrow-to-stake premium.",
        ),
    ],
    "BTC": [],
    "SOL": [],
    "USDC": [],
    "USDT": [],
}

# ─────────────────────────────────────────────────────────────────────────────
# Static transform metadata per asset family (MVP stubs)
# ─────────────────────────────────────────────────────────────────────────────

_TRANSFORMS: dict[str, list[TransformMetadata]] = {
    "ETH": [
        TransformMetadata("ETH", "stETH",  "stake",   fee_bps=0.0,   latency_seconds=12,    unbonding_days=None),
        TransformMetadata("stETH", "ETH",  "unstake", fee_bps=0.0,   latency_seconds=None,  unbonding_days=1.5),
        TransformMetadata("ETH", "wstETH", "wrap",    fee_bps=0.0,   latency_seconds=60,    unbonding_days=None),
    ],
    "SOL": [
        TransformMetadata("SOL", "mSOL",   "stake",   fee_bps=6.0,   latency_seconds=None,  unbonding_days=1.0),
        TransformMetadata("SOL", "JitoSOL", "stake",  fee_bps=4.0,   latency_seconds=None,  unbonding_days=1.0),
    ],
    "BTC": [
        TransformMetadata("BTC", "WBTC",   "wrap",    fee_bps=10.0,  latency_seconds=None,  unbonding_days=None),
        TransformMetadata("WBTC", "BTC",   "unwrap",  fee_bps=10.0,  latency_seconds=None,  unbonding_days=None),
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers to convert ORM rows → engine input types
# ─────────────────────────────────────────────────────────────────────────────


def _deriv_to_history_point(row: DerivativesSnapshot, field: str) -> HistoryPoint | None:
    v = getattr(row, field, None)
    if v is None:
        return None
    return HistoryPoint(timestamp=row.snapshot_at.replace(tzinfo=UTC), value=v)


def _lending_row_to_input(row: LendingMarketSnapshot) -> LendingMarketInput:
    return LendingMarketInput(
        protocol=row.protocol,
        market=row.market,
        chain=row.chain,
        borrow_apy=row.borrow_apy,
        utilization=row.utilization,
        available_liquidity_usd=row.available_liquidity_usd,
        tvl_usd=row.tvl_usd,
        snapshot_at=row.snapshot_at.replace(tzinfo=UTC) if row.snapshot_at else None,
    )


def _staking_to_input(row: StakingSnapshot) -> StakingInput:
    return StakingInput(
        symbol=row.symbol,
        protocol=row.protocol,
        staking_apy=row.staking_apy,
        tvl_usd=row.tvl_usd,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main loader
# ─────────────────────────────────────────────────────────────────────────────


async def load_inputs(
    db: AsyncSession,
    symbol: str,
    days: int = 30,
) -> BorrowDemandInputs:
    sym = symbol.upper()

    # Determine related lending symbols (e.g. ETH family includes WETH)
    lending_symbols = _lending_symbols(sym)

    # ── Derivatives ──────────────────────────────────────────────────────────
    current_snapshots = await get_latest_per_venue(db, sym)
    deriv_hist_rows = await deriv_history(db, sym, days=days)

    # Use the venue with the highest open interest as the "primary" current snapshot
    primary = (
        max(current_snapshots, key=lambda r: r.open_interest_usd or 0)
        if current_snapshots else None
    )

    funding_rate = primary.funding_rate if primary else None
    basis_ann = primary.basis_annualized if primary else None
    oi_usd = primary.open_interest_usd if primary else None

    funding_history = [
        p for row in deriv_hist_rows
        if (p := _deriv_to_history_point(row, "funding_rate")) is not None
    ]
    basis_history = [
        p for row in deriv_hist_rows
        if (p := _deriv_to_history_point(row, "basis_annualized")) is not None
    ]
    oi_history = [
        p for row in deriv_hist_rows
        if (p := _deriv_to_history_point(row, "open_interest_usd")) is not None
    ]

    # ── Lending ──────────────────────────────────────────────────────────────
    latest_lending = await get_latest_per_market(db, lending_symbols)
    lending_inputs = [_lending_row_to_input(r) for r in latest_lending]

    lending_hist_rows = await lending_history(db, lending_symbols, days=days)
    # Build a simple max-borrow-rate time series from history
    borrow_rate_by_ts: dict[datetime, list[float]] = {}
    for row in lending_hist_rows:
        if row.borrow_apy is not None and row.snapshot_at:
            ts = row.snapshot_at.replace(tzinfo=UTC)
            borrow_rate_by_ts.setdefault(ts, []).append(row.borrow_apy)
    borrow_rate_history = sorted(
        [
            HistoryPoint(timestamp=ts, value=max(vals))
            for ts, vals in borrow_rate_by_ts.items()
        ],
        key=lambda p: p.timestamp,
    )

    # ── Staking ──────────────────────────────────────────────────────────────
    staking_inputs = await _load_staking(db, sym)

    return BorrowDemandInputs(
        symbol=sym,
        funding_rate=funding_rate,
        basis_annualized=basis_ann,
        open_interest_usd=oi_usd,
        funding_history=funding_history,
        oi_history=oi_history,
        basis_history=basis_history,
        lending_markets=lending_inputs,
        borrow_rate_history=borrow_rate_history,
        staking=staking_inputs,
        event_overlays=_EVENT_OVERLAYS.get(sym, []),
        transforms=_TRANSFORMS.get(sym, []),
        data_window_days=days,
    )


def _lending_symbols(symbol: str) -> list[str]:
    """Map a top-level asset to the set of DeFiLlama symbols for its lending markets."""
    _MAP: dict[str, list[str]] = {
        "BTC":  ["WBTC", "CBBTC", "BTC", "BTCB"],
        "ETH":  ["ETH", "WETH", "stETH", "wstETH"],
        "SOL":  ["SOL", "mSOL", "JitoSOL"],
        "USDC": ["USDC"],
        "USDT": ["USDT"],
        "DAI":  ["DAI"],
    }
    return _MAP.get(symbol, [symbol])


async def _load_staking(db: AsyncSession, symbol: str) -> list[StakingInput]:
    """Return latest staking snapshots matching the symbol or its underlying."""
    from app.models.staking import StakingSnapshot

    sub = (
        select(
            StakingSnapshot.symbol,
            StakingSnapshot.protocol,
            func.max(StakingSnapshot.snapshot_at).label("max_at"),
        )
        .where(
            (StakingSnapshot.symbol == symbol)
            | (StakingSnapshot.underlying_symbol == symbol)
        )
        .group_by(StakingSnapshot.symbol, StakingSnapshot.protocol)
        .subquery()
    )
    stmt = select(StakingSnapshot).join(
        sub,
        (StakingSnapshot.symbol == sub.c.symbol)
        & (StakingSnapshot.protocol == sub.c.protocol)
        & (StakingSnapshot.snapshot_at == sub.c.max_at),
    )
    result = await db.execute(stmt)
    return [_staking_to_input(r) for r in result.scalars().all()]
