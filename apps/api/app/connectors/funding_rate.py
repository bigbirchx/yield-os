"""
CeFi perpetual funding-rate adapters.

One :class:`ProtocolAdapter` subclass per exchange:

  - :class:`BinanceFundingRateAdapter`  (Venue.BINANCE)
  - :class:`OkxFundingRateAdapter`      (Venue.OKX)
  - :class:`BybitFundingRateAdapter`    (Venue.BYBIT)
  - :class:`DeribitFundingRateAdapter`  (Venue.DERIBIT)

Each adapter emits one SUPPLY-side :class:`MarketOpportunity` per tracked
perpetual contract.  Positive funding means longs pay shorts; a delta-neutral
position (long spot / short perp) receives the rate.  We model this as a
SUPPLY opportunity where the yield is the current annualised funding rate.

Annualisation formula (all venues use 8-hour settlement cycles unless noted):
  total_apy_pct = raw_8h_rate × 3 × 365 × 100

Deribit is USD-settled (not USDT); rates are still expressed in percentage
terms and normalised the same way.

The ``chains`` filter is not applicable for CeFi adapters (no on-chain
deployment) and is silently ignored.

Historical data: ``historical_rates_7d`` is populated as a list of
``{"date": "YYYY-MM-DD", "value": float_pct}`` dicts using each exchange's
public funding-rate history endpoint (no API key required).

Refresh: 60 seconds.

Venue conflict note
-------------------
BINANCE, OKX, BYBIT, and DERIBIT venues are also used by the basis-trade
and CEX-earn adapters.  An :class:`AdapterRegistry` supports only one adapter
per venue; the integrating code must choose which adapter to register, or use
separate registry instances.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog

from app.connectors.base_adapter import ProtocolAdapter
from app.connectors.http_client import get_json
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

_ANNUALIZE_8H_PCT = 3 * 365 * 100.0   # raw 8-hour rate → annualised percentage

# Perpetual symbols to fetch when symbols=None
_DEFAULT_SYMBOLS: list[str] = ["BTC", "ETH", "SOL"]

# Deribit currently supports BTC, ETH, SOL perpetuals
_DERIBIT_DEFAULT_SYMBOLS: list[str] = ["BTC", "ETH", "SOL"]


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _safe_float(val: Any) -> float | None:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _daily_history(records: list[tuple[str, float]]) -> list[dict]:
    """Aggregate per-settlement records into daily averages.

    Args:
        records: list of ``(date_str, annualised_rate_pct)`` tuples.

    Returns:
        Sorted list of ``{"date": "YYYY-MM-DD", "value": float}`` dicts.
    """
    by_date: dict[str, list[float]] = {}
    for date_str, rate_pct in records:
        by_date.setdefault(date_str, []).append(rate_pct)
    return [
        {"date": d, "value": round(sum(vs) / len(vs), 6)}
        for d, vs in sorted(by_date.items())
    ]


# ═══════════════════════════════════════════════════════════════════════════
# Binance
# ═══════════════════════════════════════════════════════════════════════════


class BinanceFundingRateAdapter(ProtocolAdapter):
    """Binance USDT-margined perpetual funding-rate adapter.

    Data source: Binance FAPI (public, no API key required).
    """

    _FAPI = "https://fapi.binance.com/fapi/v1"

    # -- ProtocolAdapter properties ------------------------------------------

    @property
    def venue(self) -> Venue:
        return Venue.BINANCE

    @property
    def protocol_name(self) -> str:
        return "Binance Perpetuals"

    @property
    def protocol_slug(self) -> str:
        return "binance-perp"

    @property
    def supported_chains(self) -> list[Chain]:
        return []  # CeFi — no on-chain deployment

    @property
    def refresh_interval_seconds(self) -> int:
        return 60

    @property
    def requires_api_key(self) -> bool:
        return False

    @property
    def api_key_env_var(self) -> str | None:
        return None

    # -- Internal helpers ----------------------------------------------------

    def _perp_sym(self, canonical: str) -> str:
        return f"{canonical.upper()}USDT"

    async def _fetch_live(self, canonical: str) -> float | None:
        """Return the current annualised funding rate in percent."""
        try:
            data = await get_json(
                f"{self._FAPI}/premiumIndex",
                params={"symbol": self._perp_sym(canonical)},
            )
            raw = _safe_float(data.get("lastFundingRate"))
            return raw * _ANNUALIZE_8H_PCT if raw is not None else None
        except Exception as exc:
            log.warning("binance_funding_live_error", symbol=canonical, error=str(exc))
            return None

    async def _fetch_history_7d(self, canonical: str) -> list[dict]:
        """Return last ≤7 daily average funding rates (3 settlements/day)."""
        try:
            records = await get_json(
                f"{self._FAPI}/fundingRate",
                params={"symbol": self._perp_sym(canonical), "limit": 21},
            )
            if not isinstance(records, list):
                return []
            pairs: list[tuple[str, float]] = []
            for r in records:
                ts_ms = r.get("fundingTime")
                rate = _safe_float(r.get("fundingRate"))
                if ts_ms is None or rate is None:
                    continue
                dt = datetime.fromtimestamp(int(ts_ms) / 1000, tz=UTC)
                pairs.append((dt.strftime("%Y-%m-%d"), rate * _ANNUALIZE_8H_PCT))
            return _daily_history(pairs)
        except Exception as exc:
            log.warning("binance_funding_history_error", symbol=canonical, error=str(exc))
            return []

    async def _fetch_symbol(self, canonical: str) -> MarketOpportunity | None:
        live_pct, history = await asyncio.gather(
            self._fetch_live(canonical),
            self._fetch_history_7d(canonical),
        )
        if live_pct is None:
            return None

        perp_sym = self._perp_sym(canonical)
        return self.build_opportunity(
            asset_id=self.normalize_symbol(canonical),
            asset_symbol=canonical.upper(),
            chain="BINANCE",
            market_id=f"{perp_sym}-PERP",
            market_name=f"Binance {canonical.upper()}-USDT Perpetual",
            side=OpportunitySide.SUPPLY,
            opportunity_type=OpportunityType.FUNDING_RATE,
            effective_duration=EffectiveDuration.OVERNIGHT,
            total_apy_pct=live_pct,
            base_apy_pct=live_pct,
            reward_breakdown=[
                RewardBreakdown(
                    reward_type=RewardType.NATIVE_YIELD,
                    apy_pct=live_pct,
                    is_variable=True,
                    notes="8-hour perpetual funding rate (annualised)",
                ),
            ],
            liquidity=LiquidityInfo(),
            rate_model=RateModelInfo(
                model_type="perp-funding-8h",
                current_supply_rate_pct=live_pct,
            ),
            tags=["perpetual", "8h-funding", "usdt-margined"],
            source_url=f"https://www.binance.com/en/futures/{perp_sym}",
            historical_rates_7d=history if history else None,
        )

    # -- ProtocolAdapter interface -------------------------------------------

    async def fetch_opportunities(
        self,
        symbols: list[str] | None = None,
        chains: list[Chain] | None = None,
    ) -> list[MarketOpportunity]:
        target = symbols or _DEFAULT_SYMBOLS
        results = await asyncio.gather(
            *[self._fetch_symbol(sym) for sym in target],
            return_exceptions=True,
        )
        opps: list[MarketOpportunity] = []
        for sym, result in zip(target, results):
            if isinstance(result, Exception):
                log.warning("binance_funding_symbol_error", symbol=sym, error=str(result))
            elif result is not None:
                opps.append(result)
        log.info("binance_funding_fetch_done", opportunities=len(opps))
        return opps

    async def health_check(self) -> dict[str, Any]:
        try:
            data = await get_json(
                f"{self._FAPI}/premiumIndex", params={"symbol": "BTCUSDT"},
            )
            ok = data.get("lastFundingRate") is not None
            return {"status": "ok" if ok else "degraded", "last_success": self._last_success, "error": None}
        except Exception as exc:
            return {"status": "down", "last_success": self._last_success, "error": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════
# OKX
# ═══════════════════════════════════════════════════════════════════════════


class OkxFundingRateAdapter(ProtocolAdapter):
    """OKX USDT-linear perpetual (SWAP) funding-rate adapter.

    Data source: OKX V5 public API (no API key required).
    """

    _BASE = "https://www.okx.com/api/v5/public"

    @property
    def venue(self) -> Venue:
        return Venue.OKX

    @property
    def protocol_name(self) -> str:
        return "OKX Perpetuals"

    @property
    def protocol_slug(self) -> str:
        return "okx-perp"

    @property
    def supported_chains(self) -> list[Chain]:
        return []

    @property
    def refresh_interval_seconds(self) -> int:
        return 60

    @property
    def requires_api_key(self) -> bool:
        return False

    @property
    def api_key_env_var(self) -> str | None:
        return None

    def _inst_id(self, canonical: str) -> str:
        return f"{canonical.upper()}-USDT-SWAP"

    async def _fetch_live(self, canonical: str) -> float | None:
        try:
            data = await get_json(
                f"{self._BASE}/funding-rate",
                params={"instId": self._inst_id(canonical)},
            )
            items = data.get("data") or []
            if not items:
                return None
            raw = _safe_float(items[0].get("fundingRate"))
            return raw * _ANNUALIZE_8H_PCT if raw is not None else None
        except Exception as exc:
            log.warning("okx_funding_live_error", symbol=canonical, error=str(exc))
            return None

    async def _fetch_history_7d(self, canonical: str) -> list[dict]:
        try:
            data = await get_json(
                f"{self._BASE}/funding-rate-history",
                params={"instId": self._inst_id(canonical), "limit": 21},
            )
            items = data.get("data") or []
            pairs: list[tuple[str, float]] = []
            for r in items:
                ts_ms = r.get("fundingTime")
                # OKX provides realizedRate (settled) or fundingRate (current)
                rate_key = "realizedRate" if "realizedRate" in r else "fundingRate"
                rate = _safe_float(r.get(rate_key))
                if ts_ms is None or rate is None:
                    continue
                dt = datetime.fromtimestamp(int(ts_ms) / 1000, tz=UTC)
                pairs.append((dt.strftime("%Y-%m-%d"), rate * _ANNUALIZE_8H_PCT))
            return _daily_history(pairs)
        except Exception as exc:
            log.warning("okx_funding_history_error", symbol=canonical, error=str(exc))
            return []

    async def _fetch_symbol(self, canonical: str) -> MarketOpportunity | None:
        live_pct, history = await asyncio.gather(
            self._fetch_live(canonical),
            self._fetch_history_7d(canonical),
        )
        if live_pct is None:
            return None

        inst_id = self._inst_id(canonical)
        return self.build_opportunity(
            asset_id=self.normalize_symbol(canonical),
            asset_symbol=canonical.upper(),
            chain="OKX",
            market_id=inst_id,
            market_name=f"OKX {canonical.upper()}-USDT Perpetual",
            side=OpportunitySide.SUPPLY,
            opportunity_type=OpportunityType.FUNDING_RATE,
            effective_duration=EffectiveDuration.OVERNIGHT,
            total_apy_pct=live_pct,
            base_apy_pct=live_pct,
            reward_breakdown=[
                RewardBreakdown(
                    reward_type=RewardType.NATIVE_YIELD,
                    apy_pct=live_pct,
                    is_variable=True,
                    notes="8-hour perpetual funding rate (annualised)",
                ),
            ],
            liquidity=LiquidityInfo(),
            rate_model=RateModelInfo(
                model_type="perp-funding-8h",
                current_supply_rate_pct=live_pct,
            ),
            tags=["perpetual", "8h-funding", "usdt-margined"],
            source_url=f"https://www.okx.com/trade-swap/{canonical.lower()}-usdt-swap",
            historical_rates_7d=history if history else None,
        )

    async def fetch_opportunities(
        self,
        symbols: list[str] | None = None,
        chains: list[Chain] | None = None,
    ) -> list[MarketOpportunity]:
        target = symbols or _DEFAULT_SYMBOLS
        results = await asyncio.gather(
            *[self._fetch_symbol(sym) for sym in target],
            return_exceptions=True,
        )
        opps: list[MarketOpportunity] = []
        for sym, result in zip(target, results):
            if isinstance(result, Exception):
                log.warning("okx_funding_symbol_error", symbol=sym, error=str(result))
            elif result is not None:
                opps.append(result)
        log.info("okx_funding_fetch_done", opportunities=len(opps))
        return opps

    async def health_check(self) -> dict[str, Any]:
        try:
            data = await get_json(
                f"{self._BASE}/funding-rate", params={"instId": "BTC-USDT-SWAP"},
            )
            ok = bool(data.get("data"))
            return {"status": "ok" if ok else "degraded", "last_success": self._last_success, "error": None}
        except Exception as exc:
            return {"status": "down", "last_success": self._last_success, "error": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════
# Bybit
# ═══════════════════════════════════════════════════════════════════════════


class BybitFundingRateAdapter(ProtocolAdapter):
    """Bybit linear USDT-margined perpetual funding-rate adapter.

    Data source: Bybit V5 public API (no API key required).

    Bybit's default funding interval is 8 hours for most USDT perpetuals.
    Rates from the tickers endpoint represent the *next* funding rate.
    """

    _BASE = "https://api.bybit.com/v5/market"

    @property
    def venue(self) -> Venue:
        return Venue.BYBIT

    @property
    def protocol_name(self) -> str:
        return "Bybit Perpetuals"

    @property
    def protocol_slug(self) -> str:
        return "bybit-perp"

    @property
    def supported_chains(self) -> list[Chain]:
        return []

    @property
    def refresh_interval_seconds(self) -> int:
        return 60

    @property
    def requires_api_key(self) -> bool:
        return False

    @property
    def api_key_env_var(self) -> str | None:
        return None

    def _perp_sym(self, canonical: str) -> str:
        return f"{canonical.upper()}USDT"

    async def _fetch_live(self, canonical: str) -> float | None:
        try:
            data = await get_json(
                f"{self._BASE}/tickers",
                params={"category": "linear", "symbol": self._perp_sym(canonical)},
            )
            items = (data.get("result") or {}).get("list") or []
            if not items:
                return None
            raw = _safe_float(items[0].get("fundingRate"))
            return raw * _ANNUALIZE_8H_PCT if raw is not None else None
        except Exception as exc:
            log.warning("bybit_funding_live_error", symbol=canonical, error=str(exc))
            return None

    async def _fetch_history_7d(self, canonical: str) -> list[dict]:
        try:
            data = await get_json(
                f"{self._BASE}/funding/history",
                params={
                    "category": "linear",
                    "symbol": self._perp_sym(canonical),
                    "limit": 21,
                },
            )
            items = (data.get("result") or {}).get("list") or []
            pairs: list[tuple[str, float]] = []
            for r in items:
                ts_ms = r.get("fundingRateTimestamp")
                rate = _safe_float(r.get("fundingRate"))
                if ts_ms is None or rate is None:
                    continue
                dt = datetime.fromtimestamp(int(ts_ms) / 1000, tz=UTC)
                pairs.append((dt.strftime("%Y-%m-%d"), rate * _ANNUALIZE_8H_PCT))
            return _daily_history(pairs)
        except Exception as exc:
            log.warning("bybit_funding_history_error", symbol=canonical, error=str(exc))
            return []

    async def _fetch_symbol(self, canonical: str) -> MarketOpportunity | None:
        live_pct, history = await asyncio.gather(
            self._fetch_live(canonical),
            self._fetch_history_7d(canonical),
        )
        if live_pct is None:
            return None

        perp_sym = self._perp_sym(canonical)
        return self.build_opportunity(
            asset_id=self.normalize_symbol(canonical),
            asset_symbol=canonical.upper(),
            chain="BYBIT",
            market_id=f"{perp_sym}-PERP",
            market_name=f"Bybit {canonical.upper()}-USDT Perpetual",
            side=OpportunitySide.SUPPLY,
            opportunity_type=OpportunityType.FUNDING_RATE,
            effective_duration=EffectiveDuration.OVERNIGHT,
            total_apy_pct=live_pct,
            base_apy_pct=live_pct,
            reward_breakdown=[
                RewardBreakdown(
                    reward_type=RewardType.NATIVE_YIELD,
                    apy_pct=live_pct,
                    is_variable=True,
                    notes="8-hour perpetual funding rate (annualised)",
                ),
            ],
            liquidity=LiquidityInfo(),
            rate_model=RateModelInfo(
                model_type="perp-funding-8h",
                current_supply_rate_pct=live_pct,
            ),
            tags=["perpetual", "8h-funding", "usdt-margined"],
            source_url=f"https://www.bybit.com/trade/usdt/{perp_sym}",
            historical_rates_7d=history if history else None,
        )

    async def fetch_opportunities(
        self,
        symbols: list[str] | None = None,
        chains: list[Chain] | None = None,
    ) -> list[MarketOpportunity]:
        target = symbols or _DEFAULT_SYMBOLS
        results = await asyncio.gather(
            *[self._fetch_symbol(sym) for sym in target],
            return_exceptions=True,
        )
        opps: list[MarketOpportunity] = []
        for sym, result in zip(target, results):
            if isinstance(result, Exception):
                log.warning("bybit_funding_symbol_error", symbol=sym, error=str(result))
            elif result is not None:
                opps.append(result)
        log.info("bybit_funding_fetch_done", opportunities=len(opps))
        return opps

    async def health_check(self) -> dict[str, Any]:
        try:
            data = await get_json(
                f"{self._BASE}/tickers",
                params={"category": "linear", "symbol": "BTCUSDT"},
            )
            ok = bool((data.get("result") or {}).get("list"))
            return {"status": "ok" if ok else "degraded", "last_success": self._last_success, "error": None}
        except Exception as exc:
            return {"status": "down", "last_success": self._last_success, "error": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════
# Deribit
# ═══════════════════════════════════════════════════════════════════════════


class DeribitFundingRateAdapter(ProtocolAdapter):
    """Deribit USD-settled perpetual funding-rate adapter.

    Data source: Deribit V2 public API (no API key required).

    Deribit perpetuals are USD-settled (not USDT).  Rates are 8-hour
    and expressed in the same annualised percentage format as other venues
    for cross-venue comparison.  Currently covers BTC, ETH, and SOL.
    """

    _BASE = "https://www.deribit.com/api/v2/public"

    @property
    def venue(self) -> Venue:
        return Venue.DERIBIT

    @property
    def protocol_name(self) -> str:
        return "Deribit Perpetuals"

    @property
    def protocol_slug(self) -> str:
        return "deribit-perp"

    @property
    def supported_chains(self) -> list[Chain]:
        return []

    @property
    def refresh_interval_seconds(self) -> int:
        return 60

    @property
    def requires_api_key(self) -> bool:
        return False

    @property
    def api_key_env_var(self) -> str | None:
        return None

    def _instrument(self, canonical: str) -> str:
        return f"{canonical.upper()}-PERPETUAL"

    async def _fetch_live(self, canonical: str) -> float | None:
        try:
            data = await get_json(
                f"{self._BASE}/ticker",
                params={"instrument_name": self._instrument(canonical)},
            )
            result = data.get("result") or {}
            # funding_8h is the 8-hour funding rate for the current period
            raw = _safe_float(result.get("funding_8h"))
            return raw * _ANNUALIZE_8H_PCT if raw is not None else None
        except Exception as exc:
            log.warning("deribit_funding_live_error", symbol=canonical, error=str(exc))
            return None

    async def _fetch_history_7d(self, canonical: str) -> list[dict]:
        """Fetch the last 7 days of Deribit perpetual funding history."""
        import time as _time
        now_ms = int(_time.time() * 1000)
        start_ms = now_ms - 7 * 24 * 60 * 60 * 1000
        try:
            data = await get_json(
                f"{self._BASE}/get_funding_rate_history",
                params={
                    "instrument_name": self._instrument(canonical),
                    "start_timestamp": start_ms,
                    "end_timestamp": now_ms,
                },
            )
            items = data.get("result") or []
            pairs: list[tuple[str, float]] = []
            for r in items:
                ts_ms = r.get("timestamp")
                # interest_8h is the 8-hour settled rate
                rate = _safe_float(r.get("interest_8h") or r.get("interest"))
                if ts_ms is None or rate is None:
                    continue
                dt = datetime.fromtimestamp(int(ts_ms) / 1000, tz=UTC)
                pairs.append((dt.strftime("%Y-%m-%d"), rate * _ANNUALIZE_8H_PCT))
            return _daily_history(pairs)
        except Exception as exc:
            log.warning("deribit_funding_history_error", symbol=canonical, error=str(exc))
            return []

    async def _fetch_symbol(self, canonical: str) -> MarketOpportunity | None:
        live_pct, history = await asyncio.gather(
            self._fetch_live(canonical),
            self._fetch_history_7d(canonical),
        )
        if live_pct is None:
            return None

        instrument = self._instrument(canonical)
        return self.build_opportunity(
            asset_id=self.normalize_symbol(canonical),
            asset_symbol=canonical.upper(),
            chain="DERIBIT",
            market_id=instrument,
            market_name=f"Deribit {canonical.upper()}-USD Perpetual",
            side=OpportunitySide.SUPPLY,
            opportunity_type=OpportunityType.FUNDING_RATE,
            effective_duration=EffectiveDuration.OVERNIGHT,
            total_apy_pct=live_pct,
            base_apy_pct=live_pct,
            reward_breakdown=[
                RewardBreakdown(
                    reward_type=RewardType.NATIVE_YIELD,
                    apy_pct=live_pct,
                    is_variable=True,
                    notes="8-hour perpetual funding rate (annualised); USD-settled",
                ),
            ],
            liquidity=LiquidityInfo(),
            rate_model=RateModelInfo(
                model_type="perp-funding-8h",
                current_supply_rate_pct=live_pct,
            ),
            tags=["perpetual", "8h-funding", "usd-settled"],
            source_url=f"https://www.deribit.com/options/{canonical.upper()}",
            historical_rates_7d=history if history else None,
        )

    async def fetch_opportunities(
        self,
        symbols: list[str] | None = None,
        chains: list[Chain] | None = None,
    ) -> list[MarketOpportunity]:
        target = [
            sym for sym in (symbols or _DERIBIT_DEFAULT_SYMBOLS)
            if sym.upper() in ("BTC", "ETH", "SOL")
        ]
        results = await asyncio.gather(
            *[self._fetch_symbol(sym) for sym in target],
            return_exceptions=True,
        )
        opps: list[MarketOpportunity] = []
        for sym, result in zip(target, results):
            if isinstance(result, Exception):
                log.warning("deribit_funding_symbol_error", symbol=sym, error=str(result))
            elif result is not None:
                opps.append(result)
        log.info("deribit_funding_fetch_done", opportunities=len(opps))
        return opps

    async def health_check(self) -> dict[str, Any]:
        try:
            data = await get_json(
                f"{self._BASE}/ticker",
                params={"instrument_name": "BTC-PERPETUAL"},
            )
            ok = data.get("result") is not None
            return {"status": "ok" if ok else "degraded", "last_success": self._last_success, "error": None}
        except Exception as exc:
            return {"status": "down", "last_success": self._last_success, "error": str(exc)}
