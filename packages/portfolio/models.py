"""
Portfolio book data models for Yield OS.

Represents the actual institutional lending/trading desk book structure:
bilateral loans, DeFi protocol deployments, native staking, and
counterparty-level collateral with pro-rata allocation to individual loans.

Source data: CreditDesk WACC Export workbook (Asset_Params, Trades_Raw,
Observed_Collateral sheets).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class PositionCategory(str, Enum):
    """Classification of a book position by its operational nature."""

    DEFI_SUPPLY = "DEFI_SUPPLY"
    DEFI_BORROW = "DEFI_BORROW"
    NATIVE_STAKING = "NATIVE_STAKING"
    BILATERAL_LOAN_OUT = "BILATERAL_LOAN_OUT"
    BILATERAL_BORROW_IN = "BILATERAL_BORROW_IN"
    INTERNAL = "INTERNAL"
    OFF_PLATFORM = "OFF_PLATFORM"


# ---------------------------------------------------------------------------
# Collateral models
# ---------------------------------------------------------------------------


@dataclass
class AllocatedCollateral:
    """Pro-rata collateral allocation from observed counterparty collateral
    to a specific loan."""

    collateral_asset: str
    allocated_units: float
    allocated_usd: float
    allocation_weight_pct: float


@dataclass
class ObservedCollateral:
    """A single collateral holding observed at the counterparty level."""

    asof_date: datetime
    customer_id: int
    counterparty_name: str
    collateral_relationship: str  # "Pledged_To_FalconX" or "Pledged_To_Counterparty"
    collateral_asset: str  # canonical_id after normalization
    units_posted: float  # negative = posted TO counterparty
    data_source: str
    is_tri_party: bool
    custodial_venue: str


# ---------------------------------------------------------------------------
# Book position
# ---------------------------------------------------------------------------


@dataclass
class BookPosition:
    """A single position (loan/deployment) from the CreditDesk book."""

    # ── Identity ────────────────────────────────────────────────────────
    loan_id: int
    customer_id: int
    counterparty_name: str
    counterparty_legal_entity: str | None
    category: PositionCategory
    direction: str  # "Loan_Out" or "Borrow_In"

    # ── Principal ───────────────────────────────────────────────────────
    principal_asset: str  # canonical_id after normalization
    principal_qty: float
    principal_usd: float

    # ── Dates / tenor ───────────────────────────────────────────────────
    effective_date: date
    maturity_date: date | None  # None for open-term
    tenor: str  # "Open" or "Fixed"
    recall_period_days: float | None

    # ── Collateral terms ────────────────────────────────────────────────
    collateral_assets_raw: str | None  # comma-separated from CreditDesk
    initial_collateralization_ratio_pct: float | None  # e.g. 130 means 130%
    rehypothecation_allowed: bool
    collateral_substitution_allowed: bool
    is_collateralized: bool

    # ── Loan terms ──────────────────────────────────────────────────────
    loan_type: str  # lending, staking, internal, flex, edge, off_platform
    interest_rate_pct: float  # annualized %
    status: str
    query_notes: str | None

    # ── Derived (populated after import) ────────────────────────────────
    protocol_name: str | None = None
    protocol_chain: str | None = None
    umbrella_group: str | None = None
    matched_opportunity_id: str | None = None
    current_market_rate_pct: float | None = None
    rate_vs_market_bps: float | None = None
    allocated_collateral: list[AllocatedCollateral] | None = None


# ---------------------------------------------------------------------------
# Book summary
# ---------------------------------------------------------------------------


@dataclass
class BookSummary:
    """Aggregate view of the full book."""

    total_positions: int
    total_loan_out_usd: float
    total_borrow_in_usd: float
    net_book_usd: float
    defi_deployed_usd: float
    defi_borrowed_usd: float
    staking_deployed_usd: float
    bilateral_loan_out_usd: float
    bilateral_borrow_in_usd: float
    weighted_avg_lending_rate_pct: float
    weighted_avg_borrowing_rate_pct: float
    net_interest_margin_pct: float
    estimated_daily_income_usd: float
    estimated_annual_income_usd: float
    positions_by_asset: dict[str, float] = field(default_factory=dict)
    positions_by_counterparty: dict[str, float] = field(default_factory=dict)
    positions_by_category: dict[str, float] = field(default_factory=dict)
    defi_positions_vs_market: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Classification logic
# ---------------------------------------------------------------------------


def classify_position(
    direction: str,
    loan_type: str,
    counterparty_name: str,
) -> PositionCategory:
    """Classify a CreditDesk row into a PositionCategory.

    Parameters
    ----------
    direction
        ``"Loan_Out"`` or ``"Borrow_In"``
    loan_type
        One of: lending, staking, internal, flex, edge, off_platform
    counterparty_name
        Full counterparty name string from CreditDesk
    """
    lt = loan_type.strip().lower()
    name = counterparty_name.lower()

    if lt == "staking":
        if "credit desk defi" in name:
            return (
                PositionCategory.DEFI_SUPPLY
                if direction == "Loan_Out"
                else PositionCategory.DEFI_BORROW
            )
        elif "credit desk staking" in name:
            return PositionCategory.NATIVE_STAKING
        else:
            # Staking type but unclear — default based on direction
            return (
                PositionCategory.DEFI_SUPPLY
                if direction == "Loan_Out"
                else PositionCategory.DEFI_BORROW
            )

    if lt == "internal":
        return PositionCategory.INTERNAL

    if lt == "off_platform":
        return PositionCategory.OFF_PLATFORM

    # lending, flex, edge — bilateral
    if direction == "Loan_Out":
        return PositionCategory.BILATERAL_LOAN_OUT
    return PositionCategory.BILATERAL_BORROW_IN


# ---------------------------------------------------------------------------
# Protocol extraction
# ---------------------------------------------------------------------------

_CHAIN_HINTS: dict[str, str] = {
    "core eth": "ETHEREUM",
    "ethereum": "ETHEREUM",
    "eth mainnet": "ETHEREUM",
    "mainnet": "ETHEREUM",
    "solana": "SOLANA",
    "sol": "SOLANA",
    "base": "BASE",
    "arbitrum": "ARBITRUM",
    "optimism": "OPTIMISM",
    "polygon": "POLYGON",
    "avalanche": "AVALANCHE",
    "bsc": "BSC",
    "tron": "TRON",
    "hyperliquid": "HYPERLIQUID",
}

# Pattern: "Protocol Name (Chain Hint) - Credit Desk ..."
_PROTOCOL_PATTERN = re.compile(
    r"^(.+?)\s*(?:\(([^)]+)\))?\s*-\s*Credit\s+Desk",
    re.IGNORECASE,
)


def extract_protocol_info(
    counterparty_name: str,
) -> tuple[str | None, str | None]:
    """Extract protocol name and chain from a CreditDesk counterparty name.

    Returns ``(protocol_name, chain)`` where chain is a normalised string
    like ``"ETHEREUM"``, ``"SOLANA"``, etc.

    Examples::

        >>> extract_protocol_info("Aave v3 (Core ETH) - Credit Desk DeFi")
        ('Aave v3', 'ETHEREUM')
        >>> extract_protocol_info("Morpho - Credit Desk DeFi")
        ('Morpho', 'ETHEREUM')
        >>> extract_protocol_info("Kamino v2 (Solana) - Credit Desk DeFi")
        ('Kamino v2', 'SOLANA')
        >>> extract_protocol_info("FalconX Custody (Credit Vault) - Credit Desk Staking")
        ('FalconX Custody', None)
    """
    m = _PROTOCOL_PATTERN.match(counterparty_name.strip())
    if m is None:
        return None, None

    protocol_name = m.group(1).strip()
    chain_hint = m.group(2)
    chain: str | None = None

    if chain_hint:
        hint_lower = chain_hint.strip().lower()
        chain = _CHAIN_HINTS.get(hint_lower)
        # If not a known chain hint, leave chain as None
        # (e.g. "Credit Vault", "SYRUP Protocol Staking")

    # Default DeFi positions to ETHEREUM if no chain specified
    if chain is None and "credit desk defi" in counterparty_name.lower():
        chain = "ETHEREUM"

    return protocol_name, chain


# ---------------------------------------------------------------------------
# CreditDesk asset normalization
# ---------------------------------------------------------------------------

_CREDITDESK_SYMBOL_MAP: dict[str, str] = {
    "LOCKED-SOL": "SOL",
    "JITOSOL": "jitoSOL",
    "MSOL": "mSOL",
    "BSOL": "bSOL",
    "CBBTC": "cbBTC",
    "CBETH": "cbETH",
    "WSTETH": "wstETH",
    "STETH": "stETH",
    "WEETH": "weETH",
    "EETH": "eETH",
    "RETH": "rETH",
    "METH": "mETH",
    "SWETH": "swETH",
    "SDAI": "sDAI",
    "SUSDS": "sUSDS",
    "SUSDE": "sUSDe",
    "USDE": "USDe",
}


def normalize_creditdesk_symbol(raw_symbol: str) -> str:
    """Normalise a CreditDesk asset symbol to a canonical_id.

    Handles case-insensitive lookups, special CreditDesk conventions
    (e.g. ``LOCKED-SOL``), and falls through to the raw symbol for
    standard tickers (BTC, ETH, SOL, USDC, etc.).
    """
    stripped = raw_symbol.strip()
    upper = stripped.upper()

    # Exact match in special map
    if upper in _CREDITDESK_SYMBOL_MAP:
        return _CREDITDESK_SYMBOL_MAP[upper]

    # Standard symbols pass through as-is (preserving original case
    # for canonical_ids that are uppercase)
    return stripped


# ---------------------------------------------------------------------------
# Chain inference for assets
# ---------------------------------------------------------------------------

_ASSET_CHAIN_DEFAULTS: dict[str, str] = {
    "SOL": "SOLANA",
    "mSOL": "SOLANA",
    "jitoSOL": "SOLANA",
    "bSOL": "SOLANA",
    "INF": "SOLANA",
    "JupSOL": "SOLANA",
    "JLP": "SOLANA",
    "HYPE": "HYPERLIQUID",
    "BTC": "BITCOIN",
    "ADA": "CARDANO",
    "XRP": "XRP_LEDGER",
    "XLM": "STELLAR",
    "NEAR": "NEAR",
    "TAO": "TAO",
    "DOT": "POLKADOT",
    "ATOM": "COSMOS",
    "AVAX": "AVALANCHE",
    "LTC": "LITECOIN",
}


def infer_chain_for_asset(
    canonical_id: str,
    protocol_chain: str | None = None,
) -> str | None:
    """Infer the blockchain for a given asset.

    *protocol_chain* takes precedence if set (e.g. a Kamino position
    on Solana forces SOLANA even for USDC).  Otherwise falls back to
    asset-level defaults, then to ``"ETHEREUM"`` for known ERC-20 tokens.
    """
    if protocol_chain:
        return protocol_chain

    if canonical_id in _ASSET_CHAIN_DEFAULTS:
        return _ASSET_CHAIN_DEFAULTS[canonical_id]

    # Most ERC-20 / stablecoin tokens default to Ethereum
    from asset_registry.taxonomy import ASSET_REGISTRY

    asset_def = ASSET_REGISTRY.get(canonical_id)
    if asset_def and asset_def.native_chains:
        return asset_def.native_chains[0].value

    return None


# ---------------------------------------------------------------------------
# Collateral pro-rata allocation
# ---------------------------------------------------------------------------


def allocate_collateral(
    counterparty_loans: list[BookPosition],
    counterparty_collateral: list[ObservedCollateral],
    asset_prices: dict[str, float],
) -> dict[int, list[AllocatedCollateral]]:
    """Allocate observed collateral pro-rata across a counterparty's loans.

    Parameters
    ----------
    counterparty_loans
        All loans for a single counterparty (same customer_id).
    counterparty_collateral
        All observed collateral for that counterparty.
    asset_prices
        Canonical_id to USD price mapping.

    Returns
    -------
    dict mapping loan_id to a list of AllocatedCollateral entries.

    Allocation rules
    ----------------
    1. Each collateralised loan's requirement =
       ``principal_usd * (initial_collateralization_ratio_pct / 100)``.
    2. Positive ``units_posted`` (pledged TO us) is allocated to
       ``Loan_Out`` positions only.
    3. Negative ``units_posted`` (we posted TO counterparty) is allocated
       to ``Borrow_In`` positions only.
    4. Within each group, allocation is proportional to collateral
       requirement USD.
    """
    result: dict[int, list[AllocatedCollateral]] = {}

    # Separate loans by direction and filter to collateralised only
    loan_out_loans = [
        p for p in counterparty_loans
        if p.direction == "Loan_Out"
        and p.is_collateralized
        and p.initial_collateralization_ratio_pct is not None
        and p.initial_collateralization_ratio_pct > 0
    ]
    borrow_in_loans = [
        p for p in counterparty_loans
        if p.direction == "Borrow_In"
        and p.is_collateralized
        and p.initial_collateralization_ratio_pct is not None
        and p.initial_collateralization_ratio_pct > 0
    ]

    # Separate collateral by sign
    collateral_to_us = [c for c in counterparty_collateral if c.units_posted > 0]
    collateral_to_them = [c for c in counterparty_collateral if c.units_posted < 0]

    def _allocate_group(
        loans: list[BookPosition],
        collateral: list[ObservedCollateral],
    ) -> None:
        if not loans or not collateral:
            return

        # Compute per-loan requirement
        requirements: dict[int, float] = {}
        for loan in loans:
            ratio = loan.initial_collateralization_ratio_pct or 0.0
            requirements[loan.loan_id] = loan.principal_usd * (ratio / 100.0)

        total_req = sum(requirements.values())
        if total_req <= 0:
            return

        # Compute weights
        weights: dict[int, float] = {
            lid: req / total_req for lid, req in requirements.items()
        }

        # Allocate each collateral line item
        for col in collateral:
            price = asset_prices.get(col.collateral_asset, 0.0)
            units = abs(col.units_posted)

            for loan in loans:
                w = weights[loan.loan_id]
                alloc_units = units * w
                alloc_usd = alloc_units * price

                if loan.loan_id not in result:
                    result[loan.loan_id] = []

                result[loan.loan_id].append(AllocatedCollateral(
                    collateral_asset=col.collateral_asset,
                    allocated_units=round(alloc_units, 8),
                    allocated_usd=round(alloc_usd, 2),
                    allocation_weight_pct=round(w * 100.0, 4),
                ))

    _allocate_group(loan_out_loans, collateral_to_us)
    _allocate_group(borrow_in_loans, collateral_to_them)

    return result


# ---------------------------------------------------------------------------
# Book summary computation
# ---------------------------------------------------------------------------


def compute_summary(
    positions: list[BookPosition],
) -> BookSummary:
    """Compute aggregate metrics from a list of book positions."""
    total_loan_out = 0.0
    total_borrow_in = 0.0
    defi_deployed = 0.0
    defi_borrowed = 0.0
    staking_deployed = 0.0
    bilateral_out = 0.0
    bilateral_in = 0.0

    # For weighted averages
    lending_rate_x_usd = 0.0
    lending_usd = 0.0
    borrowing_rate_x_usd = 0.0
    borrowing_usd = 0.0

    by_asset: dict[str, float] = {}
    by_counterparty: dict[str, float] = {}
    by_category: dict[str, float] = {}
    defi_vs_market: list[dict] = []

    for p in positions:
        usd = p.principal_usd
        cat = p.category.value

        # Direction totals
        if p.direction == "Loan_Out":
            total_loan_out += usd
            lending_rate_x_usd += p.interest_rate_pct * usd
            lending_usd += usd
        else:
            total_borrow_in += usd
            borrowing_rate_x_usd += p.interest_rate_pct * usd
            borrowing_usd += usd

        # Category totals
        if p.category == PositionCategory.DEFI_SUPPLY:
            defi_deployed += usd
        elif p.category == PositionCategory.DEFI_BORROW:
            defi_borrowed += usd
        elif p.category == PositionCategory.NATIVE_STAKING:
            staking_deployed += usd
        elif p.category == PositionCategory.BILATERAL_LOAN_OUT:
            bilateral_out += usd
        elif p.category == PositionCategory.BILATERAL_BORROW_IN:
            bilateral_in += usd

        # Net exposure by asset (loan_out positive, borrow_in negative)
        sign = 1.0 if p.direction == "Loan_Out" else -1.0
        by_asset[p.principal_asset] = by_asset.get(p.principal_asset, 0.0) + sign * usd

        # Gross USD by counterparty
        by_counterparty[p.counterparty_name] = (
            by_counterparty.get(p.counterparty_name, 0.0) + usd
        )

        # Gross USD by category
        by_category[cat] = by_category.get(cat, 0.0) + usd

        # DeFi market comparison
        if p.category in (
            PositionCategory.DEFI_SUPPLY,
            PositionCategory.DEFI_BORROW,
        ) and p.current_market_rate_pct is not None:
            defi_vs_market.append({
                "loan_id": p.loan_id,
                "protocol_name": p.protocol_name,
                "protocol_chain": p.protocol_chain,
                "principal_asset": p.principal_asset,
                "direction": p.direction,
                "our_rate_pct": p.interest_rate_pct,
                "market_rate_pct": p.current_market_rate_pct,
                "rate_diff_bps": p.rate_vs_market_bps,
                "principal_usd": usd,
            })

    wa_lending = lending_rate_x_usd / lending_usd if lending_usd > 0 else 0.0
    wa_borrowing = borrowing_rate_x_usd / borrowing_usd if borrowing_usd > 0 else 0.0
    nim = wa_lending - wa_borrowing

    # Income estimates
    net_book = total_loan_out - total_borrow_in
    annual_income = (
        (lending_rate_x_usd - borrowing_rate_x_usd) / 100.0
    )
    daily_income = annual_income / 365.0

    return BookSummary(
        total_positions=len(positions),
        total_loan_out_usd=round(total_loan_out, 2),
        total_borrow_in_usd=round(total_borrow_in, 2),
        net_book_usd=round(net_book, 2),
        defi_deployed_usd=round(defi_deployed, 2),
        defi_borrowed_usd=round(defi_borrowed, 2),
        staking_deployed_usd=round(staking_deployed, 2),
        bilateral_loan_out_usd=round(bilateral_out, 2),
        bilateral_borrow_in_usd=round(bilateral_in, 2),
        weighted_avg_lending_rate_pct=round(wa_lending, 4),
        weighted_avg_borrowing_rate_pct=round(wa_borrowing, 4),
        net_interest_margin_pct=round(nim, 4),
        estimated_daily_income_usd=round(daily_income, 2),
        estimated_annual_income_usd=round(annual_income, 2),
        positions_by_asset=by_asset,
        positions_by_counterparty=by_counterparty,
        positions_by_category=by_category,
        defi_positions_vs_market=defi_vs_market,
    )
