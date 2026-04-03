"""
Multi-venue dated-futures basis-trade adapter.

Wraps :func:`app.services.basis_service.get_basis_snapshot` and emits one
SUPPLY-side :class:`MarketOpportunity` per active dated-futures contract
across Deribit, Binance, OKX, Bybit, and CME.

Basis-trade model
-----------------
A basis trade captures the premium of the dated futures contract over the
spot (index) price by simultaneously holding long spot and short futures.
The annualised spread is locked in at entry and realised at expiry.

  basis_pct_ann  = (futures_price − index_price) / index_price × (365 / DTE)

Negative basis (backwardation) is emitted as a negative ``total_apy_pct``
and tagged ``backwardation`` for downstream filtering.

Opportunity fields
------------------
  opportunity_type  = BASIS_TRADE
  side              = SUPPLY   (capital deployed into the spread)
  effective_duration = FIXED_TERM
  total_apy_pct     = basis_pct_ann × 100
  maturity_date     = contract expiry datetime (UTC)
  days_to_maturity  = days to expiry at snapshot time
  market_id         = contract label, e.g. ``"BTC-28MAR25"``

Per-row venue override
----------------------
The adapter's primary :attr:`venue` is ``Venue.DERIBIT`` (used as the
AdapterRegistry key).  Each individual :class:`MarketOpportunity` carries
the correct exchange in its ``venue`` field (DERIBIT, BINANCE, OKX, BYBIT,
or CME), overriding the adapter default via :meth:`build_opportunity` kwargs.

CME note
--------
CME data requires an Amberdata derivatives subscription.  The basis_service
skips CME gracefully when the key is absent; this adapter inherits that
behaviour.

Refresh: 300 seconds.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import structlog

from app.connectors.base_adapter import ProtocolAdapter
from app.services.basis_service import BasisRow, get_basis_snapshot
from asset_registry import Chain, Venue
from opportunity_schema import (
    EffectiveDuration,
    LiquidityInfo,
    MarketOpportunity,
    OpportunitySide,
    OpportunityType,
    RateModelInfo,
    RewardBreakdown,
    RewardType,
)

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Symbols to track when symbols=None
_DEFAULT_SYMBOLS: list[str] = ["BTC", "ETH"]

# Venue string (from BasisRow) → Venue enum
_VENUE_ENUM: dict[str, Venue] = {
    "deribit": Venue.DERIBIT,
    "binance": Venue.BINANCE,
    "okx":     Venue.OKX,
    "bybit":   Venue.BYBIT,
    "cme":     Venue.CME,
}

_PROTOCOL_SLUG: dict[str, str] = {
    "deribit": "deribit-futures",
    "binance": "binance-futures",
    "okx":     "okx-futures",
    "bybit":   "bybit-futures",
    "cme":     "cme-futures",
}

_PROTOCOL_NAME: dict[str, str] = {
    "deribit": "Deribit Dated Futures",
    "binance": "Binance USDT Delivery Futures",
    "okx":     "OKX USDT Futures",
    "bybit":   "Bybit Linear Futures",
    "cme":     "CME Bitcoin Futures",
}

# Pseudo-chain value stored on each opportunity
_CHAIN: dict[str, str] = {
    "deribit": "DERIBIT",
    "binance": "BINANCE",
    "okx":     "OKX",
    "bybit":   "BYBIT",
    "cme":     "CME",
}


# ---------------------------------------------------------------------------
# Source URL helpers
# ---------------------------------------------------------------------------

def _source_url(venue: str, contract: str, symbol: str) -> str:
    """Return a human-readable URL for a dated-futures contract."""
    sym = symbol.upper()
    if venue == "deribit":
        return f"https://www.deribit.com/futures/{contract}"
    if venue == "binance":
        return f"https://www.binance.com/en/delivery/{sym}_USD"
    if venue == "okx":
        return f"https://www.okx.com/trade-futures/{sym.lower()}-usdt"
    if venue == "bybit":
        return f"https://www.bybit.com/trade/futures/usdt/{sym}"
    if venue == "cme":
        return "https://www.cmegroup.com/markets/cryptocurrencies/bitcoin/bitcoin-futures.html"
    return ""


# ═══════════════════════════════════════════════════════════════════════════
# Adapter
# ═══════════════════════════════════════════════════════════════════════════


class BasisTradeAdapter(ProtocolAdapter):
    """Multi-venue dated-futures basis-trade adapter.

    Wraps :func:`~app.services.basis_service.get_basis_snapshot` and converts
    :class:`~app.services.basis_service.BasisRow` instances into
    :class:`~opportunity_schema.MarketOpportunity` objects.

    Primary venue is :attr:`Venue.DERIBIT` for the AdapterRegistry key.
    Each emitted opportunity carries its actual exchange in ``venue``.
    """

    # -- ProtocolAdapter properties ------------------------------------------

    @property
    def venue(self) -> Venue:
        return Venue.DERIBIT  # Primary for AdapterRegistry; overridden per-row below

    @property
    def protocol_name(self) -> str:
        return "Dated Futures Basis"

    @property
    def protocol_slug(self) -> str:
        return "basis-trade"

    @property
    def supported_chains(self) -> list[Chain]:
        return []  # CeFi — no on-chain deployment

    @property
    def refresh_interval_seconds(self) -> int:
        return 300

    @property
    def requires_api_key(self) -> bool:
        return False  # CME gracefully skipped if Amberdata key absent

    @property
    def api_key_env_var(self) -> str | None:
        return None

    # -- Internal helpers ----------------------------------------------------

    def _row_to_opportunity(
        self,
        row: BasisRow,
        canonical_symbol: str,
    ) -> MarketOpportunity | None:
        """Convert a single :class:`BasisRow` into a :class:`MarketOpportunity`."""
        if row.basis_pct_ann is None:
            return None
        if row.days_to_expiry <= 0:
            return None

        venue_key = row.venue.lower()
        row_venue = _VENUE_ENUM.get(venue_key, Venue.DERIBIT)
        protocol_slug = _PROTOCOL_SLUG.get(venue_key, "basis-trade")
        protocol_name = _PROTOCOL_NAME.get(venue_key, f"{row.venue.capitalize()} Futures")
        chain = _CHAIN.get(venue_key, row.venue.upper())

        basis_apy_pct = row.basis_pct_ann * 100.0

        try:
            expiry_dt = datetime.fromisoformat(row.expiry)
        except Exception:
            log.warning("basis_expiry_parse_error", contract=row.contract, expiry=row.expiry)
            return None

        tags = ["basis-trade", "dated-futures", venue_key]
        if basis_apy_pct < 0:
            tags.append("backwardation")

        return self.build_opportunity(
            # Override adapter-level identity to reflect this row's exchange
            venue=row_venue.value,
            protocol=protocol_name,
            protocol_slug=protocol_slug,
            data_source=protocol_slug,
            # Market identity
            asset_id=canonical_symbol,
            asset_symbol=canonical_symbol.upper(),
            chain=chain,
            market_id=row.contract,
            market_name=f"{protocol_name} {row.contract}",
            # Opportunity structure
            side=OpportunitySide.SUPPLY,
            opportunity_type=OpportunityType.BASIS_TRADE,
            effective_duration=EffectiveDuration.FIXED_TERM,
            maturity_date=expiry_dt,
            days_to_maturity=float(row.days_to_expiry),
            # Yield
            total_apy_pct=basis_apy_pct,
            base_apy_pct=basis_apy_pct,
            reward_breakdown=[
                RewardBreakdown(
                    reward_type=RewardType.NATIVE_YIELD,
                    apy_pct=basis_apy_pct,
                    is_variable=False,
                    notes=(
                        f"Basis locked at entry; "
                        f"futures={row.futures_price:.2f}, "
                        f"spot={row.index_price:.2f}, "
                        f"DTE={row.days_to_expiry}"
                    ),
                ),
            ],
            # Liquidity — use OI as a proxy for available liquidity
            liquidity=LiquidityInfo(
                available_liquidity_usd=row.oi_usd,
            ),
            rate_model=RateModelInfo(
                model_type="cash-futures-basis",
                current_supply_rate_pct=basis_apy_pct,
            ),
            tags=tags,
            source_url=_source_url(venue_key, row.contract, canonical_symbol),
        )

    # -- ProtocolAdapter interface -------------------------------------------

    async def fetch_opportunities(
        self,
        symbols: list[str] | None = None,
        chains: list[Chain] | None = None,
    ) -> list[MarketOpportunity]:
        """Fetch dated-futures term structures across all tracked venues.

        Fetches snapshots for each symbol in parallel.  Each snapshot covers
        Deribit, Binance, OKX, Bybit, and CME (where available).
        """
        target = symbols or _DEFAULT_SYMBOLS

        results = await asyncio.gather(
            *[get_basis_snapshot(sym) for sym in target],
            return_exceptions=True,
        )

        all_opps: list[MarketOpportunity] = []
        for sym, result in zip(target, results):
            if isinstance(result, Exception):
                log.warning("basis_snapshot_error", symbol=sym, error=str(result))
                continue
            rows: list[BasisRow] = result
            canonical = self.normalize_symbol(sym)
            for row in rows:
                try:
                    opp = self._row_to_opportunity(row, canonical)
                    if opp is not None:
                        all_opps.append(opp)
                except Exception as exc:
                    log.warning(
                        "basis_row_parse_error",
                        symbol=sym,
                        contract=row.contract,
                        venue=row.venue,
                        error=str(exc),
                    )

        log.info(
            "basis_trade_fetch_done",
            opportunities=len(all_opps),
            symbols=target,
        )
        return all_opps

    async def health_check(self) -> dict[str, Any]:
        """Probe Deribit (primary venue) for a BTC snapshot."""
        try:
            rows = await get_basis_snapshot("BTC")
            deribit_rows = [r for r in rows if r.venue == "deribit"]
            ok = len(deribit_rows) > 0
            return {
                "status": "ok" if ok else "degraded",
                "last_success": self._last_success,
                "error": None,
            }
        except Exception as exc:
            return {
                "status": "down",
                "last_success": self._last_success,
                "error": str(exc),
            }
