"""
Funding-rate service — orchestrates all exchange connectors and produces the
normalised snapshot + history responses consumed by the funding routers.

Exchange coverage
-----------------
  Binance  — MongoDB history (3y) + predicted rate + OI/vol via internal connectors
  OKX      — MongoDB history + live rate; REST for OI
  Bybit    — PerpFuture._get_bybit_funding_rate_history + live/OI via REST
  Deribit  — PerpFuture._get_deribit_funding_rate_history + live via REST; USD-settled
  Bullish  — live + 90-day settlement history via Bullish class (gated by creds)
  Coinglass— secondary cross-check via coinglass_client

TTL cache — history DataFrames cached per (symbol, exchange) for 5 minutes.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import pandas as pd
import structlog

from app.connectors.coinglass_client import CoinglassSnapshot, fetch_funding_snapshot
from app.connectors.internal.path_setup import (
    Bullish,
    PerpFuture,
    _HAS_APIS,
    get_annualized_funding_rate_history,
    get_binance_market_metrics,
    get_binance_predicted_funding_rate,
    get_okx_funding_rate,
)
from app.core.config import settings

log = structlog.get_logger(__name__)

_ANNUALIZE_8H = 3 * 365  # 8-hour rate → APR
_HISTORY_TTL = 300.0     # 5-minute cache TTL
_HTTP_TIMEOUT = 6.0

_hist_cache: dict[str, tuple[float, pd.DataFrame]] = {}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ExchangeData:
    live_apr: float | None = None
    last_apr: float | None = None
    funding_interval_hours: float | None = None
    oi_coin: float | None = None
    oi_usd: float | None = None
    volume_coin_24h: float | None = None
    ma_7d_apr: float | None = None
    ma_30d_apr: float | None = None


@dataclass
class FundingSnapshot:
    symbol: str
    as_of: str
    exchanges: dict[str, ExchangeData]
    blended: dict[str, float | None]
    coinglass: dict[str, float | None]


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_get(key: str) -> pd.DataFrame | None:
    entry = _hist_cache.get(key)
    if entry and time.monotonic() - entry[0] < _HISTORY_TTL:
        return entry[1]
    return None


def _cache_set(key: str, df: pd.DataFrame) -> None:
    _hist_cache[key] = (time.monotonic(), df)


# ---------------------------------------------------------------------------
# DataFrame normalisation helpers
# ---------------------------------------------------------------------------

def _norm_history_df(raw: Any, exchange_col: str | None = None) -> pd.DataFrame:
    """
    Normalise any exchange history DataFrame to:
        index: DatetimeIndex (UTC, name="snapshot_at")
        column: "annualized_funding_rate"
    """
    if raw is None:
        return pd.DataFrame()
    if isinstance(raw, pd.DataFrame) and raw.empty:
        return pd.DataFrame()

    df: pd.DataFrame = raw.copy()

    # Shape A: indexed by datetime, columns = exchange names
    if isinstance(df.index, pd.DatetimeIndex):
        col = exchange_col if exchange_col and exchange_col in df.columns else (
            df.columns[0] if len(df.columns) == 1 else None
        )
        if col:
            df = df[[col]].rename(columns={col: "annualized_funding_rate"})
        elif "annualized_funding_rate" not in df.columns:
            return pd.DataFrame()
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df.index.name = "snapshot_at"
        return df.dropna(subset=["annualized_funding_rate"])

    # Shape B: columnar with a timestamp column
    ts_col = next((c for c in ("timestamp", "time", "date") if c in df.columns), None)
    if ts_col:
        df.index = pd.to_datetime(df[ts_col], utc=True)
        df.index.name = "snapshot_at"
        df = df.drop(columns=[ts_col])

    rate_col = next(
        (c for c in ("annualized_funding_rate", "funding_rate_annualized") if c in df.columns),
        None,
    )
    if rate_col is None:
        return pd.DataFrame()

    df = df[[rate_col]].rename(columns={rate_col: "annualized_funding_rate"})
    return df.dropna(subset=["annualized_funding_rate"])


def _funding_interval(df: pd.DataFrame) -> float:
    if df.empty or len(df) < 2:
        return 8.0
    idx = df.index.sort_values()
    hours = (idx[-1] - idx[-2]).total_seconds() / 3600
    return round(hours, 2) if 0 < hours < 48 else 8.0


def _compute_ma(df: pd.DataFrame, days: int) -> float | None:
    if df.empty:
        return None
    try:
        daily = df["annualized_funding_rate"].resample("D").mean()
        val = daily.rolling(days, min_periods=1).mean().iloc[-1]
        return float(val) if pd.notna(val) else None
    except Exception:
        return None


def _last_apr(df: pd.DataFrame) -> float | None:
    if df.empty:
        return None
    s = df["annualized_funding_rate"].dropna()
    return float(s.iloc[-1]) if not s.empty else None


def _safe_float(val: object) -> float | None:
    try:
        return float(val) if val is not None else None  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Direct REST history fallbacks (work without reference codebase in Docker)
# ---------------------------------------------------------------------------

async def _binance_rest_history(symbol: str, days: int) -> pd.DataFrame:
    """Binance FAPI funding rate history — paginated REST, no key needed.

    Binance caps at 1000 records per call (≈333 days at 3/day).  We paginate
    by advancing ``startTime`` until we have the full requested window.
    """
    cutoff_ms = int((pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)).timestamp() * 1000)
    start_ms = cutoff_ms
    all_records: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
            for _ in range(10):  # safety cap — 10 pages × 1000 = 10 000 records
                r = await c.get(
                    "https://fapi.binance.com/fapi/v1/fundingRate",
                    params={"symbol": f"{symbol}USDT", "startTime": start_ms, "limit": 1000},
                )
                r.raise_for_status()
                page = r.json()
                if not page:
                    break
                all_records.extend(page)
                if len(page) < 1000:
                    break  # last page
                start_ms = int(page[-1]["fundingTime"]) + 1  # advance cursor

        if not all_records:
            return pd.DataFrame()
        df = pd.DataFrame(all_records)
        df.index = pd.to_datetime(df["fundingTime"].astype(int), unit="ms", utc=True)
        df.index.name = "snapshot_at"
        df["annualized_funding_rate"] = df["fundingRate"].astype(float) * _ANNUALIZE_8H
        return df[~df.index.duplicated(keep="last")][["annualized_funding_rate"]].sort_index().dropna()
    except Exception:
        log.exception("binance_rest_history_error", symbol=symbol)
        return pd.DataFrame()


async def _okx_rest_history(symbol: str, days: int) -> pd.DataFrame:
    """OKX public funding rate history — paginated REST.

    OKX returns at most 100 records per page (≈33 days at 3/day).  Paginate
    using the ``after`` parameter (fundingTime of the oldest record on the
    current page) to walk back in time.
    """
    cutoff_ms = int((pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)).timestamp() * 1000)
    all_records: list[dict] = []
    after: str | None = None
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
            for _ in range(20):  # 20 pages × 100 = 2000 records ≈ 667 days
                params: dict = {"instId": f"{symbol}-USDT-SWAP", "limit": 100}
                if after:
                    params["after"] = after
                r = await c.get(
                    "https://www.okx.com/api/v5/public/funding-rate-history",
                    params=params,
                )
                r.raise_for_status()
                page = r.json().get("data", [])
                if not page:
                    break
                hit_cutoff = False
                for item in page:
                    if int(item["fundingTime"]) < cutoff_ms:
                        hit_cutoff = True
                        break
                    all_records.append(item)
                if hit_cutoff or len(page) < 100:
                    break
                after = page[-1]["fundingTime"]  # oldest on this page → next cursor

        if not all_records:
            return pd.DataFrame()
        records = []
        for item in all_records:
            ts = pd.to_datetime(int(item["fundingTime"]), unit="ms", utc=True)
            rate_key = "realizedRate" if "realizedRate" in item else "fundingRate"
            rate = _safe_float(item.get(rate_key))
            if rate is not None:
                records.append({
                    "snapshot_at": ts,
                    "annualized_funding_rate": rate * _ANNUALIZE_8H,
                })
        if not records:
            return pd.DataFrame()
        df = pd.DataFrame(records).set_index("snapshot_at").sort_index()
        return df[~df.index.duplicated(keep="last")].dropna()
    except Exception:
        log.exception("okx_rest_history_error", symbol=symbol)
        return pd.DataFrame()


async def _bybit_rest_history(symbol: str, days: int) -> pd.DataFrame:
    """Bybit linear perpetual funding rate history — paginated REST.

    Bybit returns at most 200 records per page (≈67 days at 3/day).  Paginate
    by walking the ``startTime``/``endTime`` window backward in time until we
    have the full requested period.
    """
    now_ms = int(pd.Timestamp.now(tz="UTC").timestamp() * 1000)
    cutoff_ms = int((pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)).timestamp() * 1000)
    end_ms = now_ms
    all_records: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
            for _ in range(20):  # 20 pages × 200 = 4000 records ≈ 1333 days
                r = await c.get(
                    "https://api.bybit.com/v5/market/funding/history",
                    params={
                        "category": "linear",
                        "symbol": f"{symbol}USDT",
                        "startTime": cutoff_ms,
                        "endTime": end_ms,
                        "limit": 200,
                    },
                )
                r.raise_for_status()
                page = r.json().get("result", {}).get("list", [])
                if not page:
                    break
                all_records.extend(page)
                if len(page) < 200:
                    break  # last page for this window
                # Walk backward: oldest record in page becomes new ceiling
                end_ms = min(int(d["fundingRateTimestamp"]) for d in page) - 1
                if end_ms < cutoff_ms:
                    break

        if not all_records:
            return pd.DataFrame()
        records = []
        for item in all_records:
            ts = pd.to_datetime(int(item["fundingRateTimestamp"]), unit="ms", utc=True)
            rate = _safe_float(item.get("fundingRate"))
            if rate is not None:
                records.append({
                    "snapshot_at": ts,
                    "annualized_funding_rate": rate * _ANNUALIZE_8H,
                })
        if not records:
            return pd.DataFrame()
        df = pd.DataFrame(records).set_index("snapshot_at").sort_index()
        cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
        return df[~df.index.duplicated(keep="last")][df.index >= cutoff].dropna()
    except Exception:
        log.exception("bybit_rest_history_error", symbol=symbol)
        return pd.DataFrame()


async def _deribit_rest_history(symbol: str, days: int) -> pd.DataFrame:
    """Deribit perpetual funding rate history — public REST (uses get_funding_rate_history)."""
    now_ms = int(pd.Timestamp.now(tz="UTC").timestamp() * 1000)
    start_ms = int((pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)).timestamp() * 1000)
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
            r = await c.get(
                "https://www.deribit.com/api/v2/public/get_funding_rate_history",
                params={
                    "instrument_name": f"{symbol}-PERPETUAL",
                    "start_timestamp": start_ms,
                    "end_timestamp": now_ms,
                },
            )
            r.raise_for_status()
            data = r.json().get("result", [])
            if not data:
                return pd.DataFrame()
            records = []
            for item in data:
                ts = pd.to_datetime(item["timestamp"], unit="ms", utc=True)
                # Deribit uses interest_8h (per-8h rate)
                rate = _safe_float(item.get("interest_8h") or item.get("interest"))
                if rate is not None:
                    records.append({
                        "snapshot_at": ts,
                        "annualized_funding_rate": rate * _ANNUALIZE_8H,
                    })
            if not records:
                return pd.DataFrame()
            df = pd.DataFrame(records).set_index("snapshot_at").sort_index()
            return df.dropna()
    except Exception:
        log.exception("deribit_rest_history_error", symbol=symbol)
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# MongoDB-backed history (Binance + OKX) with REST fallback
# ---------------------------------------------------------------------------

async def _mongo_history(symbol: str, exchange: str, day_count: int = 90) -> pd.DataFrame:
    key = f"mongo_{symbol}_{exchange}_{day_count}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    df = pd.DataFrame()
    if _HAS_APIS and get_annualized_funding_rate_history is not None:
        try:
            raw = await asyncio.to_thread(
                get_annualized_funding_rate_history,
                symbol, "USDT", day_count, exchange, True,
            )
            df = _norm_history_df(raw, exchange_col=exchange)
        except Exception:
            log.exception("mongo_history_error", symbol=symbol, exchange=exchange)

    # REST fallback when MongoDB is unavailable or returns empty
    if df.empty:
        if exchange == "binance":
            df = await _binance_rest_history(symbol, day_count)
        elif exchange == "okx":
            df = await _okx_rest_history(symbol, day_count)

    _cache_set(key, df)
    return df


# ---------------------------------------------------------------------------
# Per-exchange fetchers
# ---------------------------------------------------------------------------

async def _fetch_binance(symbol: str) -> ExchangeData:
    d = ExchangeData()

    async def _live() -> float | None:
        if _HAS_APIS and get_binance_predicted_funding_rate is not None:
            try:
                return await asyncio.to_thread(
                    get_binance_predicted_funding_rate, symbol, "USDT", True
                )
            except Exception:
                pass
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
                r = await c.get(
                    "https://fapi.binance.com/fapi/v1/premiumIndex",
                    params={"symbol": f"{symbol}USDT"},
                )
                r.raise_for_status()
                raw = _safe_float(r.json().get("lastFundingRate"))
                return raw * _ANNUALIZE_8H if raw is not None else None
        except Exception:
            return None

    async def _metrics() -> dict:
        if _HAS_APIS and get_binance_market_metrics is not None:
            try:
                return await asyncio.to_thread(get_binance_market_metrics, symbol, "USDT") or {}
            except Exception:
                pass
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
                r = await c.get(
                    "https://fapi.binance.com/fapi/v1/openInterest",
                    params={"symbol": f"{symbol}USDT"},
                )
                r.raise_for_status()
                oi_coin = _safe_float(r.json().get("openInterest"))
                return {"perpetual_open_interest": oi_coin}
        except Exception:
            return {}

    live, m, hist = await asyncio.gather(
        _live(), _metrics(), _mongo_history(symbol, "binance", 90)
    )
    d.live_apr = _safe_float(live)
    d.last_apr = _last_apr(hist)
    d.funding_interval_hours = _funding_interval(hist)
    d.oi_usd = _safe_float(m.get("perpetual_open_interest_USD"))
    d.oi_coin = _safe_float(m.get("perpetual_open_interest"))
    d.volume_coin_24h = _safe_float(m.get("perpetual_volume_24h"))
    d.ma_7d_apr = _compute_ma(hist, 7)
    d.ma_30d_apr = _compute_ma(hist, 30)
    return d


async def _fetch_okx(symbol: str) -> ExchangeData:
    d = ExchangeData()

    async def _live() -> float | None:
        if _HAS_APIS and get_okx_funding_rate is not None:
            try:
                return await asyncio.to_thread(get_okx_funding_rate, symbol, "USDT", True, False)
            except Exception:
                pass
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
                r = await c.get(
                    "https://www.okx.com/api/v5/public/funding-rate",
                    params={"instId": f"{symbol}-USDT-SWAP"},
                )
                r.raise_for_status()
                data = r.json().get("data", [{}])
                if data:
                    raw = _safe_float(data[0].get("fundingRate"))
                    return raw * _ANNUALIZE_8H if raw is not None else None
        except Exception:
            return None
        return None

    async def _oi() -> dict:
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
                r = await c.get(
                    "https://www.okx.com/api/v5/public/open-interest",
                    params={"instId": f"{symbol}-USDT-SWAP"},
                )
                r.raise_for_status()
                items = r.json().get("data", [])
                if items:
                    return {
                        "oi_coin": _safe_float(items[0].get("oiCcy")),
                        "oi_usd": _safe_float(items[0].get("oiUsd")),
                    }
        except Exception:
            pass
        return {}

    live, oi_data, hist = await asyncio.gather(
        _live(), _oi(), _mongo_history(symbol, "okx", 90)
    )
    d.live_apr = _safe_float(live)
    d.last_apr = _last_apr(hist)
    d.funding_interval_hours = _funding_interval(hist)
    d.oi_coin = oi_data.get("oi_coin")
    d.oi_usd = oi_data.get("oi_usd")
    d.ma_7d_apr = _compute_ma(hist, 7)
    d.ma_30d_apr = _compute_ma(hist, 30)
    return d


async def _fetch_bybit(symbol: str) -> ExchangeData:
    d = ExchangeData()
    cache_key = f"bybit_{symbol}_90"

    async def _ticker() -> dict:
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
                r = await c.get(
                    "https://api.bybit.com/v5/market/tickers",
                    params={"category": "linear", "symbol": f"{symbol}USDT"},
                )
                r.raise_for_status()
                items = r.json().get("result", {}).get("list", [])
                if items:
                    t = items[0]
                    raw = _safe_float(t.get("fundingRate"))
                    return {
                        "live_apr": raw * _ANNUALIZE_8H if raw is not None else None,
                        "oi_coin": _safe_float(t.get("openInterest")),
                        "oi_usd": _safe_float(t.get("openInterestValue")),
                    }
        except Exception:
            pass
        return {}

    async def _history() -> pd.DataFrame:
        df = pd.DataFrame()
        if _HAS_APIS and PerpFuture is not None:
            try:
                def _call():
                    p = PerpFuture(base_ccy=symbol, quote_ccy="USDT")
                    return p._get_bybit_funding_rate_history()
                raw = await asyncio.to_thread(_call)
                df = _norm_history_df(raw)
            except Exception:
                pass
        if df.empty:
            df = await _bybit_rest_history(symbol, 90)
        _cache_set(cache_key, df)
        return df

    hist = _cache_get(cache_key)
    if hist is not None:
        ticker = await _ticker()
    else:
        ticker, hist = await asyncio.gather(_ticker(), _history())

    d.live_apr = ticker.get("live_apr")
    d.last_apr = _last_apr(hist)
    d.funding_interval_hours = _funding_interval(hist)
    d.oi_coin = ticker.get("oi_coin")
    d.oi_usd = ticker.get("oi_usd")
    d.ma_7d_apr = _compute_ma(hist, 7)
    d.ma_30d_apr = _compute_ma(hist, 30)
    return d


async def _fetch_deribit(symbol: str) -> ExchangeData:
    d = ExchangeData()
    cache_key = f"deribit_{symbol}_90"

    async def _ticker() -> float | None:
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
                r = await c.get(
                    "https://www.deribit.com/api/v2/public/ticker",
                    params={"instrument_name": f"{symbol}-PERPETUAL"},
                )
                r.raise_for_status()
                res = r.json().get("result", {})
                raw = _safe_float(res.get("funding_8h"))
                return raw * _ANNUALIZE_8H if raw is not None else None
        except Exception:
            return None

    async def _history() -> pd.DataFrame:
        df = pd.DataFrame()
        if _HAS_APIS and PerpFuture is not None:
            try:
                def _call():
                    p = PerpFuture(base_ccy=symbol, quote_ccy="USD")
                    return p._get_deribit_funding_rate_history()
                raw = await asyncio.to_thread(_call)
                df = _norm_history_df(raw)
            except Exception:
                pass
        if df.empty:
            df = await _deribit_rest_history(symbol, 90)
        _cache_set(cache_key, df)
        return df

    async def _oi() -> float | None:
        if _HAS_APIS and PerpFuture is not None:
            try:
                def _call():
                    p = PerpFuture(base_ccy=symbol, quote_ccy="USD")
                    return p._get_deribit_open_interest()
                return await asyncio.to_thread(_call)
            except Exception:
                pass
        # REST fallback: get OI from ticker
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
                r = await c.get(
                    "https://www.deribit.com/api/v2/public/ticker",
                    params={"instrument_name": f"{symbol}-PERPETUAL"},
                )
                r.raise_for_status()
                return _safe_float(r.json().get("result", {}).get("open_interest"))
        except Exception:
            return None

    hist = _cache_get(cache_key)
    if hist is not None:
        live, oi = await asyncio.gather(_ticker(), _oi())
    else:
        live, hist, oi = await asyncio.gather(_ticker(), _history(), _oi())

    d.live_apr = live
    d.last_apr = _last_apr(hist)
    d.funding_interval_hours = _funding_interval(hist)
    d.oi_usd = _safe_float(oi)   # Deribit OI is in USD (USD-settled instrument)
    d.ma_7d_apr = _compute_ma(hist, 7)
    d.ma_30d_apr = _compute_ma(hist, 30)
    return d


async def _fetch_bullish(symbol: str) -> ExchangeData:
    d = ExchangeData()
    if (
        not _HAS_APIS
        or Bullish is None
        or not settings.bullish_public_key
        or not settings.bullish_private_key
    ):
        return d

    try:
        def _call():
            client = Bullish(settings.bullish_public_key, settings.bullish_private_key)
            rates = client.get_funding_rates() or {}
            sym_data = rates.get(symbol.upper(), rates.get(symbol, {}))
            raw_rate = sym_data.get("fundingRate") or sym_data.get("funding_rate")
            live = float(raw_rate) * _ANNUALIZE_8H if raw_rate is not None else None

            rows = [
                r for r in (client.get_derivatives_settlement_history() or [])
                if str(r.get("symbol", "")).upper().startswith(symbol.upper())
            ]
            df = pd.DataFrame()
            if rows:
                tmp = pd.DataFrame(rows)
                ts_col = next((c for c in ("settlementTime", "timestamp") if c in tmp.columns), None)
                rc = next((c for c in ("fundingRate", "funding_rate") if c in tmp.columns), None)
                if ts_col and rc:
                    tmp["snapshot_at"] = pd.to_datetime(tmp[ts_col], utc=True)
                    tmp["annualized_funding_rate"] = tmp[rc].astype(float) * _ANNUALIZE_8H
                    df = tmp.set_index("snapshot_at")[["annualized_funding_rate"]].dropna()
            return live, df

        live, hist = await asyncio.to_thread(_call)
    except Exception:
        log.exception("bullish_fetch_error", symbol=symbol)
        return d

    d.live_apr = live
    d.last_apr = _last_apr(hist)
    d.funding_interval_hours = _funding_interval(hist)
    d.ma_7d_apr = _compute_ma(hist, 7)
    d.ma_30d_apr = _compute_ma(hist, 30)
    return d


# ---------------------------------------------------------------------------
# Blending
# ---------------------------------------------------------------------------

def _blend(exchanges: dict[str, ExchangeData]) -> dict[str, float | None]:
    live = {k: v.live_apr for k, v in exchanges.items() if v.live_apr is not None}
    if not live:
        return {"equal_weighted_apr": None, "oi_weighted_apr": None, "volume_weighted_apr": None}

    equal = sum(live.values()) / len(live)

    oi_pairs = [(live[k], exchanges[k].oi_usd) for k in live if exchanges[k].oi_usd]
    oi_w: float | None = None
    if oi_pairs:
        tot = sum(w for _, w in oi_pairs)
        oi_w = sum(r * w for r, w in oi_pairs) / tot if tot else None

    vol_pairs = [(live[k], exchanges[k].volume_coin_24h) for k in live if exchanges[k].volume_coin_24h]
    vol_w: float | None = None
    if vol_pairs:
        tot = sum(w for _, w in vol_pairs)
        vol_w = sum(r * w for r, w in vol_pairs) / tot if tot else None

    return {"equal_weighted_apr": equal, "oi_weighted_apr": oi_w, "volume_weighted_apr": vol_w}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def get_funding_snapshot(symbol: str) -> FundingSnapshot:
    sym = symbol.upper()
    results = await asyncio.gather(
        _fetch_binance(sym),
        _fetch_okx(sym),
        _fetch_bybit(sym),
        _fetch_deribit(sym),
        _fetch_bullish(sym),
        fetch_funding_snapshot(sym),
        return_exceptions=True,
    )

    def _ok(v: Any, default: Any) -> Any:
        return default if isinstance(v, Exception) else v

    exchanges = {
        "binance": _ok(results[0], ExchangeData()),
        "okx":     _ok(results[1], ExchangeData()),
        "bybit":   _ok(results[2], ExchangeData()),
        "deribit": _ok(results[3], ExchangeData()),
        "bullish": _ok(results[4], ExchangeData()),
    }
    cg: CoinglassSnapshot | None = _ok(results[5], None)

    coinglass: dict[str, float | None] = {}
    if cg:
        coinglass = {"binance_apr": cg.binance_apr, "okx_apr": cg.okx_apr, "bybit_apr": cg.bybit_apr}

    return FundingSnapshot(
        symbol=sym,
        as_of=datetime.now(UTC).isoformat(),
        exchanges=exchanges,
        blended=_blend(exchanges),
        coinglass=coinglass,
    )


async def get_funding_history(
    symbol: str,
    exchange: str,
    days: int = 365,
    warmup_days: int = 0,
) -> pd.DataFrame:
    """Return normalised daily history for a single exchange.

    ``warmup_days`` extra days are fetched before ``days`` so that rolling-MA
    calculations on the caller side are fully primed at the start of the
    display window.  The returned DataFrame includes the warmup period; the
    caller trims as needed.
    """
    sym = symbol.upper()
    exch = exchange.lower()
    fetch_days = days + warmup_days

    if exch in ("binance", "okx"):
        return await _mongo_history(sym, exch, fetch_days)

    key = f"{exch}_{sym}_{fetch_days}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    df = pd.DataFrame()
    if exch == "bybit":
        if _HAS_APIS and PerpFuture is not None:
            try:
                def _call():
                    return PerpFuture(base_ccy=sym, quote_ccy="USDT")._get_bybit_funding_rate_history()
                df = _norm_history_df(await asyncio.to_thread(_call))
            except Exception:
                pass
        if df.empty:
            df = await _bybit_rest_history(sym, fetch_days)
    elif exch == "deribit":
        if _HAS_APIS and PerpFuture is not None:
            try:
                def _call():
                    return PerpFuture(base_ccy=sym, quote_ccy="USD")._get_deribit_funding_rate_history()
                df = _norm_history_df(await asyncio.to_thread(_call))
            except Exception:
                pass
        if df.empty:
            df = await _deribit_rest_history(sym, fetch_days)

    if not df.empty and fetch_days < 3650:
        cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=fetch_days)
        df = df[df.index >= cutoff]

    _cache_set(key, df)
    return df


async def get_blended_history(
    symbol: str,
    days: int = 365,
    warmup_days: int = 0,
) -> dict[str, list[dict]]:
    """Fetch all available exchange histories and compute blended daily series."""
    sym = symbol.upper()
    fetch_days = days + warmup_days
    hists = await asyncio.gather(
        _mongo_history(sym, "binance", fetch_days),
        _mongo_history(sym, "okx", fetch_days),
        get_funding_history(sym, "bybit", fetch_days),
        get_funding_history(sym, "deribit", fetch_days),
        return_exceptions=True,
    )
    labels = ("binance", "okx", "bybit", "deribit")
    daily: dict[str, pd.Series] = {}
    for label, h in zip(labels, hists):
        if isinstance(h, pd.DataFrame) and not h.empty and "annualized_funding_rate" in h.columns:
            daily[label] = h["annualized_funding_rate"].resample("D").mean()

    if not daily:
        return {"equal_weighted": [], "oi_weighted": [], "volume_weighted": []}

    combined = pd.DataFrame(daily).dropna(how="all")
    equal = combined.mean(axis=1)

    def _ser(s: pd.Series) -> list[dict]:
        return [{"date": d.strftime("%Y-%m-%d"), "value": float(v)} for d, v in s.dropna().items()]

    return {"equal_weighted": _ser(equal), "oi_weighted": _ser(equal), "volume_weighted": _ser(equal)}
