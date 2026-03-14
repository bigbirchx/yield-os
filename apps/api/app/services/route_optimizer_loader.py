"""
Input-gathering layer for the route optimizer.

Reads from DB repositories and assembles a RouteOptimizerInputs struct.
Static transform metadata and stablecoin mappings live here as well-
documented constants with explicit HOOK comments marking future extension
points.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.derivatives import get_latest_per_venue
from app.repositories.lending import get_latest_per_market
from app.services.route_optimizer import (
    LendingMarket,
    RouteAssumptions,
    RouteOptimizerInputs,
    TransformPath,
)

# ─────────────────────────────────────────────────────────────────────────────
# Static transform metadata
#
# HOOK: replace with DB-backed table (e.g. transform_paths) for desk-managed
# overrides, custom fee schedules, and venue-specific capacity limits.
# ─────────────────────────────────────────────────────────────────────────────

_TRANSFORMS_TO_TARGET: dict[str, list[TransformPath]] = {
    "stETH": [
        TransformPath(
            from_asset="ETH",
            to_asset="stETH",
            transform_type="stake",
            fee_bps=0.0,           # Lido charges 10% of staking rewards, not a borrow fee
            slippage_bps=2.0,      # minimal gas/MEV on deposit
            latency_seconds=12.0,  # one block
            unbonding_days=None,   # instant receipt of stETH
            capacity_usd=None,     # no cap on Lido deposits
        ),
    ],
    "wstETH": [
        TransformPath(
            from_asset="ETH",
            to_asset="wstETH",
            transform_type="stake",
            fee_bps=0.0,
            slippage_bps=3.0,
            latency_seconds=30.0,
            unbonding_days=None,
            capacity_usd=None,
        ),
    ],
    "ETH": [
        # Unstake stETH → ETH
        TransformPath(
            from_asset="stETH",
            to_asset="ETH",
            transform_type="unstake",
            fee_bps=0.0,
            slippage_bps=5.0,      # curve pool stETH/ETH
            latency_seconds=None,  # immediate via curve; days via Lido queue
            unbonding_days=1.5,    # Lido withdrawal queue average
            capacity_usd=5e8,      # curve pool ~$500M depth
        ),
    ],
    "WBTC": [
        TransformPath(
            from_asset="BTC",
            to_asset="WBTC",
            transform_type="wrap",
            fee_bps=10.0,          # Bitgo merchant fee ~0.10%
            slippage_bps=3.0,
            latency_seconds=None,  # BTC finality ~1h
            unbonding_days=None,
            capacity_usd=None,
        ),
    ],
    "BTC": [
        # Unwrap WBTC → BTC
        TransformPath(
            from_asset="WBTC",
            to_asset="BTC",
            transform_type="unwrap",
            fee_bps=10.0,
            slippage_bps=3.0,
            latency_seconds=None,
            unbonding_days=None,
            capacity_usd=None,
        ),
    ],
    "mSOL": [
        TransformPath(
            from_asset="SOL",
            to_asset="mSOL",
            transform_type="stake",
            fee_bps=6.0,           # Marinade 0.06% deposit fee
            slippage_bps=2.0,
            latency_seconds=None,
            unbonding_days=1.0,    # delayed unstake ~2 epochs ≈ ~4 days; liquid unstake immediate
            capacity_usd=None,
        ),
    ],
    "JitoSOL": [
        TransformPath(
            from_asset="SOL",
            to_asset="JitoSOL",
            transform_type="stake",
            fee_bps=4.0,
            slippage_bps=2.0,
            latency_seconds=None,
            unbonding_days=1.0,
            capacity_usd=None,
        ),
    ],
    "SOL": [
        # Unstake mSOL → SOL
        TransformPath(
            from_asset="mSOL",
            to_asset="SOL",
            transform_type="unstake",
            fee_bps=3.0,           # Marinade liquid unstake fee ~0.03%
            slippage_bps=5.0,
            latency_seconds=None,
            unbonding_days=0.5,    # liquid unstake ~12h
            capacity_usd=1e8,
        ),
    ],
}

# ─────────────────────────────────────────────────────────────────────────────
# Symbol mappings
# ─────────────────────────────────────────────────────────────────────────────

# DeFiLlama symbols to query for stablecoin markets
_STABLE_SYMBOLS = ["USDC", "USDT", "DAI"]

# Related symbols to load lending markets for (source legs in transforms)
_TRANSFORM_SOURCE_SYMBOLS: dict[str, list[str]] = {
    "stETH":   ["ETH", "WETH"],
    "wstETH":  ["ETH", "WETH"],
    "ETH":     ["stETH", "wstETH"],
    "WBTC":    ["BTC", "WBTC"],
    "BTC":     ["WBTC"],
    "mSOL":    ["SOL"],
    "JitoSOL": ["SOL"],
    "SOL":     ["mSOL", "JitoSOL"],
}

# Top-level asset → DeFiLlama symbols (same as borrow_demand_loader)
_TARGET_SYMBOLS: dict[str, list[str]] = {
    "BTC":    ["WBTC", "BTC", "BTCB"],
    "WBTC":   ["WBTC"],
    "ETH":    ["ETH", "WETH"],
    "stETH":  ["stETH"],
    "wstETH": ["wstETH"],
    "SOL":    ["SOL"],
    "mSOL":   ["mSOL"],
    "USDC":   ["USDC"],
    "USDT":   ["USDT"],
    "DAI":    ["DAI"],
}


# ─────────────────────────────────────────────────────────────────────────────
# ORM → input type converters
# ─────────────────────────────────────────────────────────────────────────────


def _orm_to_lending_market(row) -> LendingMarket:
    from datetime import UTC
    return LendingMarket(
        protocol=row.protocol,
        market=row.market,
        chain=getattr(row, "chain", None),
        borrow_apy=row.borrow_apy,
        supply_apy=row.supply_apy,
        utilization=row.utilization,
        available_liquidity_usd=row.available_liquidity_usd,
        tvl_usd=row.tvl_usd,
        snapshot_at=row.snapshot_at.replace(tzinfo=UTC) if row.snapshot_at else None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main loader
# ─────────────────────────────────────────────────────────────────────────────


async def load_inputs(
    db: AsyncSession,
    symbol: str,
    request_size_usd: float,
    assumptions_override: dict | None = None,
) -> RouteOptimizerInputs:
    sym = symbol.upper()
    ass = RouteAssumptions(**(assumptions_override or {}))

    # ── Target asset lending markets ────────────────────────────────────────
    target_syms = _TARGET_SYMBOLS.get(sym, [sym])
    target_rows = await get_latest_per_market(db, target_syms)
    target_markets = [_orm_to_lending_market(r) for r in target_rows]

    # ── Stablecoin lending markets ───────────────────────────────────────────
    stable_rows = await get_latest_per_market(db, _STABLE_SYMBOLS)
    stable_markets = [_orm_to_lending_market(r) for r in stable_rows]

    # ── Derivatives ──────────────────────────────────────────────────────────
    deriv_rows = await get_latest_per_venue(db, sym)
    primary_deriv = (
        max(deriv_rows, key=lambda r: r.open_interest_usd or 0)
        if deriv_rows else None
    )
    funding_rate = primary_deriv.funding_rate if primary_deriv else None
    basis_ann = primary_deriv.basis_annualized if primary_deriv else None
    oi_usd = primary_deriv.open_interest_usd if primary_deriv else None

    # ── Transform source markets ─────────────────────────────────────────────
    source_sym_set = _TRANSFORM_SOURCE_SYMBOLS.get(sym, [])
    transform_source_markets: dict[str, list[LendingMarket]] = {}
    if source_sym_set:
        source_rows = await get_latest_per_market(db, source_sym_set)
        for row in source_rows:
            transform_source_markets.setdefault(row.symbol, []).append(
                _orm_to_lending_market(row)
            )

    transforms = _TRANSFORMS_TO_TARGET.get(sym, [])

    return RouteOptimizerInputs(
        target_asset=sym,
        request_size_usd=request_size_usd,
        target_markets=target_markets,
        stable_markets=stable_markets,
        funding_rate=funding_rate,
        basis_annualized=basis_ann,
        open_interest_usd=oi_usd,
        transform_source_markets=transform_source_markets,
        transforms_to_target=transforms,
        assumptions=ass,
    )
