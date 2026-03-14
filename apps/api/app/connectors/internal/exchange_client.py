"""
Typed client wrapping the internal exchange connectors.

All public methods are async; synchronous library calls are dispatched via
asyncio.to_thread so they don't block the event loop.  Every method returns
an empty result (empty DataFrame / 0.0 / {}) if the internal paths are
unavailable — callers must not assume data will be present.

Output conventions
------------------
- All timestamps are UTC-aware datetime64 values.
- Funding rates are *annualized* floats (e.g. 0.15 = 15 % p.a.).
- Column names follow the derivatives_snapshot schema where applicable:
    symbol, venue, funding_rate, funding_rate_annualized, open_interest_usd,
    perp_volume_usd, spot_volume_usd, mark_price, snapshot_at
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import structlog

from app.connectors.internal.path_setup import (
    _HAS_APIS,
    _calc_RV_from_df,
    get_annualized_funding_rate_history,
    get_binance_market_metrics,
    get_binance_predicted_funding_rate,
    get_okx_funding_rate,
    get_okx_market_metrics,
    get_perp_mark_price_ohlc,
    get_rv,
    get_xccy_funding_rate_history,
)

log = structlog.get_logger(__name__)

_EMPTY_DF: pd.DataFrame = pd.DataFrame()


def _now_utc() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_funding_rate_history(
    base_ccy: str,
    exchange: str = "",
    day_count: int = 365,
) -> pd.DataFrame:
    """
    Return daily annualized funding-rate history.

    PRIMARY source: MongoDB (get_annualized_funding_rate_history) — covers
    Binance + OKX back to ~3 years for actively tracked tokens (USDT pairs).

    Returns a DataFrame with columns: [timestamp, symbol, venue,
    funding_rate_annualized].  Returns an empty DataFrame when unavailable.
    """
    if not _HAS_APIS or get_annualized_funding_rate_history is None:
        log.warning("internal_apis_unavailable", method="get_funding_rate_history")
        return _EMPTY_DF

    def _call() -> pd.DataFrame:
        df = get_annualized_funding_rate_history(
            base_ccy=base_ccy,
            quote_ccy="USDT",
            day_count=day_count,
            exchange=exchange,
            output_funding_rates_only=True,
        )
        return df

    try:
        raw: pd.DataFrame = await asyncio.to_thread(_call)
    except Exception:
        log.exception("funding_rate_history_error", base_ccy=base_ccy, exchange=exchange)
        return _EMPTY_DF

    if raw is None or raw.empty:
        return _EMPTY_DF

    # raw is indexed by datetime, columns = exchange names
    # Melt to long form: [snapshot_at, venue, funding_rate_annualized]
    if not isinstance(raw.index, pd.DatetimeIndex):
        raw.index = pd.to_datetime(raw.index, utc=True)
    if raw.index.tz is None:
        raw.index = raw.index.tz_localize("UTC")

    melted = raw.reset_index().melt(
        id_vars=raw.index.name or "index",
        var_name="venue",
        value_name="funding_rate_annualized",
    )
    ts_col = melted.columns[0]
    melted = melted.rename(columns={ts_col: "snapshot_at"})
    melted["symbol"] = base_ccy.upper()
    melted["funding_rate"] = melted["funding_rate_annualized"] / (365 * 3)  # ~8-hour rate
    return melted[["snapshot_at", "symbol", "venue", "funding_rate", "funding_rate_annualized"]].dropna()


async def get_current_funding_rate(
    base_ccy: str,
    exchange: str,
) -> float:
    """
    Return the current (next-period predicted) annualized funding rate.

    Supports: 'binance', 'okx'.  Falls back to direct REST when reference
    codebase is unavailable. Returns 0.0 on failure or unsupported venue.
    """
    import httpx as _httpx

    exchange_lower = exchange.lower()
    _ANNUALIZE_8H = 3 * 365

    if _HAS_APIS:
        def _call() -> float:
            if exchange_lower == "binance" and get_binance_predicted_funding_rate is not None:
                result = get_binance_predicted_funding_rate(base_ccy, "USDT", annualized=True)
                return float(result) if result is not None else 0.0
            if exchange_lower == "okx" and get_okx_funding_rate is not None:
                result = get_okx_funding_rate(base_ccy, "USDT", annualized=True, details=False)
                return float(result) if result is not None else 0.0
            return 0.0

        try:
            result = await asyncio.to_thread(_call)
            if result != 0.0:
                return result
        except Exception:
            log.exception("current_funding_rate_error", base_ccy=base_ccy, exchange=exchange)

    # Direct REST fallback (works without reference codebase)
    try:
        async with _httpx.AsyncClient(timeout=6.0) as c:
            if exchange_lower == "binance":
                r = await c.get(
                    "https://fapi.binance.com/fapi/v1/premiumIndex",
                    params={"symbol": f"{base_ccy.upper()}USDT"},
                )
                r.raise_for_status()
                raw = r.json().get("lastFundingRate")
                return float(raw) * _ANNUALIZE_8H if raw is not None else 0.0
            if exchange_lower == "okx":
                r = await c.get(
                    "https://www.okx.com/api/v5/public/funding-rate",
                    params={"instId": f"{base_ccy.upper()}-USDT-SWAP"},
                )
                r.raise_for_status()
                data = r.json().get("data", [{}])
                raw = data[0].get("fundingRate") if data else None
                return float(raw) * _ANNUALIZE_8H if raw is not None else 0.0
    except Exception:
        log.exception("current_funding_rate_rest_error", base_ccy=base_ccy, exchange=exchange)

    return 0.0


async def get_xccy_funding_spread(
    base_ccy: str,
    quote_ccy: str,
    day_count: int = 90,
) -> pd.DataFrame:
    """
    Return the cross-currency funding-rate spread between base_ccy and
    quote_ccy denominated in quote_ccy (e.g. ETH vs BTC).

    Returns a DataFrame indexed by timestamp with columns = exchange names.
    """
    if not _HAS_APIS or get_xccy_funding_rate_history is None:
        log.warning("internal_apis_unavailable", method="get_xccy_funding_spread")
        return _EMPTY_DF

    def _call() -> pd.DataFrame:
        return get_xccy_funding_rate_history(base_ccy, quote_ccy, day_count=day_count)

    try:
        df: pd.DataFrame = await asyncio.to_thread(_call)
        if df is None:
            return _EMPTY_DF
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index, utc=True)
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df.index.name = "snapshot_at"
        return df
    except Exception:
        log.exception("xccy_funding_spread_error", base_ccy=base_ccy, quote_ccy=quote_ccy)
        return _EMPTY_DF


async def get_perp_mark_price_ohlc(
    base_ccy: str,
    days_lookback: int = 90,
) -> pd.DataFrame:
    """
    Return perp mark-price OHLC (tries Binance FAPI, falls back to Bybit).

    Columns: timestamp (UTC-aware), open, high, low, close.
    """
    if not _HAS_APIS or get_perp_mark_price_ohlc is None:
        log.warning("internal_apis_unavailable", method="get_perp_mark_price_ohlc")
        return _EMPTY_DF

    def _call() -> pd.DataFrame:
        return get_perp_mark_price_ohlc(base_ccy, "USDT", days_lookback=days_lookback)  # type: ignore[misc]

    try:
        df: pd.DataFrame = await asyncio.to_thread(_call)
        if df is None or df.empty:
            return _EMPTY_DF
        if "timestamp" in df.columns and df["timestamp"].dtype != "datetime64[ns, UTC]":
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        return df
    except Exception:
        log.exception("perp_mark_price_ohlc_error", base_ccy=base_ccy)
        return _EMPTY_DF


async def get_realized_vol(
    base_ccy: str,
    day_counts: list[int] | None = None,
) -> pd.DataFrame:
    """
    Return realized volatility for base_ccy.

    Columns include: timestamp, close, log_returns, c2c_vol_{N},
    parkinson_vol_{N} for each N in day_counts.
    """
    if day_counts is None:
        day_counts = [7, 30, 90]

    if not _HAS_APIS or get_rv is None:
        log.warning("internal_apis_unavailable", method="get_realized_vol")
        return _EMPTY_DF

    def _call() -> pd.DataFrame:
        return get_rv(base_ccy, "USDT", day_counts=day_counts)  # type: ignore[misc]

    try:
        df: pd.DataFrame = await asyncio.to_thread(_call)
        if df is None or df.empty:
            return _EMPTY_DF
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        return df
    except Exception:
        log.exception("realized_vol_error", base_ccy=base_ccy)
        return _EMPTY_DF


async def get_market_metrics(
    base_ccy: str,
    exchange: str,
) -> dict[str, Any]:
    """
    Return OI and volume metrics for a perpetual futures market.

    Keys (where available): perpetual_open_interest_USD, perpetual_volume_24h_USD,
    spot_volume_24h_USD, success.  Returns {} on failure. Falls back to direct
    REST when reference codebase is unavailable.
    """
    import httpx as _httpx

    exchange_lower = exchange.lower()

    if _HAS_APIS:
        def _call() -> dict[str, Any]:
            if exchange_lower == "binance" and get_binance_market_metrics is not None:
                return get_binance_market_metrics(base_ccy, "USDT") or {}
            if exchange_lower == "okx" and get_okx_market_metrics is not None:
                return get_okx_market_metrics(base_ccy, "USDT") or {}
            return {}

        try:
            result = await asyncio.to_thread(_call)
            if result:
                return result
        except Exception:
            log.exception("market_metrics_error", base_ccy=base_ccy, exchange=exchange)

    # Direct REST fallback
    try:
        async with _httpx.AsyncClient(timeout=6.0) as c:
            sym = base_ccy.upper()
            if exchange_lower == "binance":
                r = await c.get(
                    "https://fapi.binance.com/fapi/v1/openInterest",
                    params={"symbol": f"{sym}USDT"},
                )
                r.raise_for_status()
                oi = r.json().get("openInterest")
                return {"perpetual_open_interest": float(oi)} if oi else {}
            if exchange_lower == "okx":
                r = await c.get(
                    "https://www.okx.com/api/v5/public/open-interest",
                    params={"instId": f"{sym}-USDT-SWAP"},
                )
                r.raise_for_status()
                items = r.json().get("data", [])
                if items:
                    return {
                        "perpetual_open_interest": float(items[0].get("oiCcy", 0)),
                        "perpetual_open_interest_USD": float(items[0].get("oiUsd", 0)),
                    }
    except Exception:
        log.exception("market_metrics_rest_error", base_ccy=base_ccy, exchange=exchange)

    return {}
