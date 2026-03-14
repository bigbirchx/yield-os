"""
Dated futures basis service.

Venues
------
  deribit  -- sys.path injection: get_ad_listed_expiry_term_structure (snapshot),
              get_listed_basis_history (history), up to 89 days
  binance  -- direct REST: CURRENT_QUARTER + NEXT_QUARTER USDT-margined contracts
  okx      -- direct REST: linear USDT-settled FUTURES instruments
  bybit    -- direct REST: LinearFutures category, Trading status
  cme      -- Amberdata REST (graceful skip when subscription unavailable)

Basis formula (consistent across all venues):
  basis_usd      = futures_price - index_price
  basis_pct_term = basis_usd / index_price
  basis_pct_ann  = basis_pct_term * (365 / days_to_expiry)   # None when DTE <= 0

All annualized values are decimals (0.05 = 5%).
Snapshot cached in-memory for 60 seconds per symbol.
"""
from __future__ import annotations

import asyncio
import calendar
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

# ---- Reference repo path injection (read-only) ------------------------------
_EXODUS_ROOT = Path("/home/ec2-user/workspace/exodus")
_GRIFF_COMMON = Path("/home/ec2-user/workspace/griff/common")

for _p in [_EXODUS_ROOT, _GRIFF_COMMON]:
    if _p.exists() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

try:
    from api_wrappers.ad_derivs_funcs import (  # type: ignore[import]
        get_ad_listed_expiry_term_structure as _deribit_term_structure,
        get_listed_basis_history as _deribit_basis_history,
    )
    from keys import AD_DERIVS_KEY as _AD_DERIVS_KEY  # type: ignore[import]

    _HAS_DERIBIT = True
except Exception as _e:
    _deribit_term_structure = None
    _deribit_basis_history = None
    _AD_DERIVS_KEY = None
    _HAS_DERIBIT = False
    log.warning("deribit_basis_import_failed", error=str(_e))

# ---- Constants ---------------------------------------------------------------
_SNAPSHOT_TTL = 60.0
_HTTP_TIMEOUT = 10.0

_snap_cache: dict[str, tuple[float, list]] = {}


# ---- Data models -------------------------------------------------------------

@dataclass
class BasisRow:
    venue: str
    contract: str
    expiry: str
    days_to_expiry: int
    futures_price: float
    index_price: float
    basis_usd: float
    basis_pct_ann: float | None
    oi_coin: float | None = None
    oi_usd: float | None = None
    volume_24h_usd: float | None = None


@dataclass
class BasisHistoryPoint:
    timestamp: str
    basis_usd: float | None
    basis_pct_ann: float | None
    futures_price: float | None
    index_price: float | None
    days_to_expiry: float | None


# ---- Helpers -----------------------------------------------------------------

def _safe_float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _fmt_contract(sym: str, expiry: datetime) -> str:
    return f"{sym.upper()}-{expiry.strftime('%d%b%y').upper()}"


def _parse_contract_expiry(contract: str) -> datetime | None:
    try:
        date_part = contract.split("-")[-1]
        return datetime.strptime(date_part, "%d%b%y").replace(hour=8, tzinfo=UTC)
    except Exception:
        return None


def _dte(expiry: datetime) -> int:
    return max(0, (expiry - datetime.now(UTC)).days)


def _basis_ann(basis_usd: float, index_price: float, dte: int) -> float | None:
    if dte <= 0 or not index_price:
        return None
    return (basis_usd / index_price) * (365 / dte)


def _third_friday(year: int, month: int) -> datetime:
    cal = calendar.monthcalendar(year, month)
    fridays = [week[calendar.FRIDAY] for week in cal if week[calendar.FRIDAY] != 0]
    return datetime(year, month, fridays[2], 8, 0, tzinfo=UTC)


def _next_cme_expiries(n: int = 4) -> list[tuple[str, datetime]]:
    now = datetime.now(UTC)
    result: list[tuple[str, datetime]] = []
    for year in [now.year, now.year + 1, now.year + 2]:
        for month in (3, 6, 9, 12):
            exp = _third_friday(year, month)
            if exp > now:
                code = f"BTCUSD_{exp.strftime('%y%m%d')}"
                result.append((code, exp))
    return result[:n]


# ---- Cache -------------------------------------------------------------------

def _snap_cache_get(symbol: str) -> list[BasisRow] | None:
    entry = _snap_cache.get(symbol)
    if entry and time.monotonic() - entry[0] < _SNAPSHOT_TTL:
        return entry[1]
    return None


def _snap_cache_set(symbol: str, rows: list[BasisRow]) -> None:
    _snap_cache[symbol] = (time.monotonic(), rows)


# ---- Per-venue snapshot fetchers ---------------------------------------------

async def _fetch_deribit_snapshot(symbol: str) -> list[BasisRow]:
    if not _HAS_DERIBIT or _deribit_term_structure is None:
        return []
    sym = symbol.upper()
    try:
        import pandas as pd

        raw = await asyncio.to_thread(_deribit_term_structure, sym)
        if raw is None or (hasattr(raw, "empty") and raw.empty):
            return []
        rows: list[BasisRow] = []
        for _, r in raw.iterrows():
            exp_ts = r.get("expirationTimestamp")
            if exp_ts is None:
                continue
            expiry_dt = pd.to_datetime(exp_ts, utc=True).to_pydatetime()
            dte = _dte(expiry_dt)
            if dte <= 0:
                continue
            fp = _safe_float(r.get("underlyingPrice"))
            ip = _safe_float(r.get("indexPrice"))
            if fp is None or ip is None:
                continue
            busd = fp - ip
            oi_coin = _safe_float(r.get("openInterest"))
            rows.append(BasisRow(
                venue="deribit",
                contract=_fmt_contract(sym, expiry_dt),
                expiry=expiry_dt.isoformat(),
                days_to_expiry=dte,
                futures_price=fp,
                index_price=ip,
                basis_usd=busd,
                basis_pct_ann=_basis_ann(busd, ip, dte),
                oi_coin=oi_coin,
                oi_usd=oi_coin * ip if oi_coin else None,
            ))
        return rows
    except Exception:
        log.exception("deribit_snapshot_error", symbol=sym)
        return []


async def _fetch_binance_snapshot(symbol: str) -> list[BasisRow]:
    sym = symbol.upper()
    rows: list[BasisRow] = []
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
            ei = await c.get("https://fapi.binance.com/fapi/v1/exchangeInfo")
            ei.raise_for_status()
            contracts = [
                s for s in ei.json().get("symbols", [])
                if s.get("baseAsset", "").upper() == sym
                and s.get("contractType") in ("CURRENT_QUARTER", "NEXT_QUARTER")
                and s.get("status") == "TRADING"
            ]
            for ct in contracts:
                sym_name = ct["symbol"]
                expiry_dt = datetime.fromtimestamp(
                    ct.get("deliveryDate", 0) / 1000, tz=UTC
                )
                dte = _dte(expiry_dt)
                if dte <= 0:
                    continue
                try:
                    pm_r, oi_r, vol_r = await asyncio.gather(
                        c.get("https://fapi.binance.com/fapi/v1/premiumIndex",
                              params={"symbol": sym_name}),
                        c.get("https://fapi.binance.com/fapi/v1/openInterest",
                              params={"symbol": sym_name}),
                        c.get("https://fapi.binance.com/fapi/v1/ticker/24hr",
                              params={"symbol": sym_name}),
                    )
                    pm = pm_r.json()
                    fp = _safe_float(pm.get("markPrice"))
                    ip = _safe_float(pm.get("indexPrice"))
                    if fp is None or ip is None:
                        continue
                    oi_coin = _safe_float(oi_r.json().get("openInterest"))
                    vol_usd = _safe_float(vol_r.json().get("quoteVolume"))
                    busd = fp - ip
                    rows.append(BasisRow(
                        venue="binance",
                        contract=_fmt_contract(sym, expiry_dt),
                        expiry=expiry_dt.isoformat(),
                        days_to_expiry=dte,
                        futures_price=fp,
                        index_price=ip,
                        basis_usd=busd,
                        basis_pct_ann=_basis_ann(busd, ip, dte),
                        oi_coin=oi_coin,
                        oi_usd=oi_coin * ip if oi_coin else None,
                        volume_24h_usd=vol_usd,
                    ))
                except Exception:
                    log.warning("binance_contract_fetch_error", contract=sym_name)
    except Exception:
        log.exception("binance_snapshot_error", symbol=sym)
    return rows


async def _fetch_okx_snapshot(symbol: str) -> list[BasisRow]:
    sym = symbol.upper()
    rows: list[BasisRow] = []
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
            inst_r = await c.get(
                "https://www.okx.com/api/v5/public/instruments",
                params={"instType": "FUTURES"},
            )
            inst_r.raise_for_status()
            contracts = [
                i for i in inst_r.json().get("data", [])
                if i.get("ctType") == "linear"
                and i.get("quoteCcy") == "USDT"
                and i.get("state") == "live"
                and i.get("baseCcy", "").upper() == sym
            ]
            for inst in contracts:
                inst_id = inst["instId"]
                exp_ms = _safe_float(inst.get("expTime"))
                if not exp_ms:
                    continue
                expiry_dt = datetime.fromtimestamp(exp_ms / 1000, tz=UTC)
                dte = _dte(expiry_dt)
                if dte <= 0:
                    continue
                try:
                    mp_r, oi_r, tk_r = await asyncio.gather(
                        c.get("https://www.okx.com/api/v5/public/mark-price",
                              params={"instId": inst_id, "instType": "FUTURES"}),
                        c.get("https://www.okx.com/api/v5/public/open-interest",
                              params={"instId": inst_id}),
                        c.get("https://www.okx.com/api/v5/market/ticker",
                              params={"instId": inst_id}),
                    )
                    mp_data = (mp_r.json().get("data") or [{}])[0]
                    oi_data = (oi_r.json().get("data") or [{}])[0]
                    tk_data = (tk_r.json().get("data") or [{}])[0]
                    fp = _safe_float(mp_data.get("markPx"))
                    ip = _safe_float(tk_data.get("idxPx")) or _safe_float(tk_data.get("last"))
                    if fp is None or ip is None:
                        continue
                    oi_usd = _safe_float(oi_data.get("oiUsd"))
                    oi_coin = _safe_float(oi_data.get("oi"))
                    vol_base = _safe_float(tk_data.get("volCcy24h"))
                    vol_usd = vol_base * ip if vol_base and ip else None
                    busd = fp - ip
                    rows.append(BasisRow(
                        venue="okx",
                        contract=_fmt_contract(sym, expiry_dt),
                        expiry=expiry_dt.isoformat(),
                        days_to_expiry=dte,
                        futures_price=fp,
                        index_price=ip,
                        basis_usd=busd,
                        basis_pct_ann=_basis_ann(busd, ip, dte),
                        oi_coin=oi_coin,
                        oi_usd=oi_usd,
                        volume_24h_usd=vol_usd,
                    ))
                except Exception:
                    log.warning("okx_contract_fetch_error", inst_id=inst_id)
    except Exception:
        log.exception("okx_snapshot_error", symbol=sym)
    return rows


async def _fetch_bybit_snapshot(symbol: str) -> list[BasisRow]:
    sym = symbol.upper()
    rows: list[BasisRow] = []
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
            inst_r = await c.get(
                "https://api.bybit.com/v5/market/instruments-info",
                params={"category": "linear"},
            )
            inst_r.raise_for_status()
            contracts = [
                i for i in inst_r.json().get("result", {}).get("list", [])
                if i.get("contractType") == "LinearFutures"
                and i.get("status") == "Trading"
                and i.get("baseCoin", "").upper() == sym
            ]
            for inst in contracts:
                bybit_sym = inst["symbol"]
                exp_ms = _safe_float(inst.get("deliveryTime"))
                if not exp_ms:
                    continue
                expiry_dt = datetime.fromtimestamp(exp_ms / 1000, tz=UTC)
                dte = _dte(expiry_dt)
                if dte <= 0:
                    continue
                try:
                    tk_r = await c.get(
                        "https://api.bybit.com/v5/market/tickers",
                        params={"category": "linear", "symbol": bybit_sym},
                    )
                    tk_r.raise_for_status()
                    tk = (tk_r.json().get("result", {}).get("list") or [{}])[0]
                    fp = _safe_float(tk.get("markPrice"))
                    ip = _safe_float(tk.get("indexPrice"))
                    if fp is None or ip is None:
                        continue
                    busd = fp - ip
                    rows.append(BasisRow(
                        venue="bybit",
                        contract=_fmt_contract(sym, expiry_dt),
                        expiry=expiry_dt.isoformat(),
                        days_to_expiry=dte,
                        futures_price=fp,
                        index_price=ip,
                        basis_usd=busd,
                        basis_pct_ann=_basis_ann(busd, ip, dte),
                        oi_coin=_safe_float(tk.get("openInterest")),
                        oi_usd=_safe_float(tk.get("openInterestValue")),
                        volume_24h_usd=_safe_float(tk.get("turnover24h")),
                    ))
                except Exception:
                    log.warning("bybit_contract_fetch_error", symbol=bybit_sym)
    except Exception:
        log.exception("bybit_snapshot_error", symbol=sym)
    return rows


async def _fetch_cme_snapshot(symbol: str) -> list[BasisRow]:
    """
    Attempt CME term structure via Amberdata OHLCV.
    Uses latest daily close as futures price; Binance spot as BRR index proxy.
    Skips gracefully on 401/403 or missing subscription.
    """
    if not _HAS_DERIBIT or not _AD_DERIVS_KEY or symbol.upper() != "BTC":
        log.warning("cme_snapshot_skipped", reason="no_key_or_non_btc")
        return []
    sym = symbol.upper()
    index_price: float | None = None
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            spot = await c.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": f"{sym}USDT"},
            )
            index_price = _safe_float(spot.json().get("price"))
    except Exception:
        log.warning("cme_index_proxy_failed")

    if not index_price:
        return []

    rows: list[BasisRow] = []
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
        for _, expiry_dt in _next_cme_expiries(4):
            dte = _dte(expiry_dt)
            if dte <= 0:
                continue
            full_code = f"BTCUSD_{expiry_dt.strftime('%y%m%d')}"
            try:
                resp = await c.get(
                    f"https://api.amberdata.com/markets/futures/ohlcv/{full_code}",
                    params={"exchange": "cme", "timeInterval": "days"},
                    headers={"x-api-key": _AD_DERIVS_KEY, "accept": "application/json"},
                )
                if resp.status_code in (401, 403):
                    log.warning("cme_amberdata_no_subscription", status=resp.status_code)
                    break
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                data = resp.json().get("payload", {}).get("data", [])
                if not data:
                    continue
                fp = _safe_float(data[-1].get("close"))
                if fp is None:
                    continue
                busd = fp - index_price
                rows.append(BasisRow(
                    venue="cme",
                    contract=_fmt_contract(sym, expiry_dt),
                    expiry=expiry_dt.isoformat(),
                    days_to_expiry=dte,
                    futures_price=fp,
                    index_price=index_price,
                    basis_usd=busd,
                    basis_pct_ann=_basis_ann(busd, index_price, dte),
                ))
            except Exception:
                log.warning("cme_contract_fetch_error", contract=full_code)
    return rows


# ---- Per-venue history fetchers ----------------------------------------------

async def _deribit_history(symbol: str, contract: str, days: int) -> list[BasisHistoryPoint]:
    if not _HAS_DERIBIT or _deribit_basis_history is None:
        return []
    expiry_dt = _parse_contract_expiry(contract)
    if not expiry_dt:
        return []
    lookback = min(days, 89)
    try:
        df = await asyncio.to_thread(_deribit_basis_history, symbol.upper(), expiry_dt, lookback)
        if df is None or (hasattr(df, "empty") and df.empty):
            return []
        result = []
        for ts, row in df.iterrows():
            ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            result.append(BasisHistoryPoint(
                timestamp=ts_str,
                basis_usd=_safe_float(row.get("basis_USD")),
                basis_pct_ann=_safe_float(row.get("basis_%_annualized")),
                futures_price=_safe_float(row.get("underlyingPrice")),
                index_price=_safe_float(row.get("indexPrice")),
                days_to_expiry=_safe_float(row.get("daysToExpiration")),
            ))
        return result
    except Exception:
        log.exception("deribit_history_error", contract=contract)
        return []


async def _binance_history(symbol: str, contract: str, days: int) -> list[BasisHistoryPoint]:
    """
    Daily klines; index price from current premiumIndex as snapshot proxy
    (Binance FAPI has no historical per-candle index price endpoint).
    """
    expiry_dt = _parse_contract_expiry(contract)
    if not expiry_dt:
        return []
    sym = symbol.upper()
    binance_sym = f"{sym}USDT_{expiry_dt.strftime('%y%m%d')}"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
            klines_r, pm_r = await asyncio.gather(
                c.get("https://fapi.binance.com/fapi/v1/klines",
                      params={"symbol": binance_sym, "interval": "1d",
                              "limit": min(days, 500)}),
                c.get("https://fapi.binance.com/fapi/v1/premiumIndex",
                      params={"symbol": binance_sym}),
            )
            klines = klines_r.json()
            ip_now = _safe_float(pm_r.json().get("indexPrice"))
            if not isinstance(klines, list) or not ip_now:
                return []
            result = []
            for k in klines:
                ts_ms = k[0]
                fp = _safe_float(k[4])
                if fp is None:
                    continue
                ts = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
                dte_hist = max(0, (expiry_dt - ts).days)
                busd = fp - ip_now
                result.append(BasisHistoryPoint(
                    timestamp=ts.isoformat(),
                    basis_usd=busd,
                    basis_pct_ann=_basis_ann(busd, ip_now, dte_hist),
                    futures_price=fp,
                    index_price=ip_now,
                    days_to_expiry=float(dte_hist),
                ))
            return result
    except Exception:
        log.exception("binance_history_error", contract=contract)
        return []


async def _okx_history(symbol: str, contract: str, days: int) -> list[BasisHistoryPoint]:
    expiry_dt = _parse_contract_expiry(contract)
    if not expiry_dt:
        return []
    sym = symbol.upper()
    okx_id = f"{sym}-USDT-{expiry_dt.strftime('%y%m%d')}"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
            candles_r, tk_r = await asyncio.gather(
                c.get("https://www.okx.com/api/v5/market/candles",
                      params={"instId": okx_id, "bar": "1D",
                              "limit": min(days, 300)}),
                c.get("https://www.okx.com/api/v5/market/ticker",
                      params={"instId": okx_id}),
            )
            candles = candles_r.json().get("data", [])
            tk = (tk_r.json().get("data") or [{}])[0]
            ip_now = _safe_float(tk.get("idxPx")) or _safe_float(tk.get("last"))
            if not candles or not ip_now:
                return []
            result = []
            for c_row in candles:
                ts_ms = _safe_float(c_row[0])
                fp = _safe_float(c_row[4])
                if ts_ms is None or fp is None:
                    continue
                ts = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
                dte_hist = max(0, (expiry_dt - ts).days)
                busd = fp - ip_now
                result.append(BasisHistoryPoint(
                    timestamp=ts.isoformat(),
                    basis_usd=busd,
                    basis_pct_ann=_basis_ann(busd, ip_now, dte_hist),
                    futures_price=fp,
                    index_price=ip_now,
                    days_to_expiry=float(dte_hist),
                ))
            return sorted(result, key=lambda x: x.timestamp)
    except Exception:
        log.exception("okx_history_error", contract=contract)
        return []


async def _bybit_history(symbol: str, contract: str, days: int) -> list[BasisHistoryPoint]:
    expiry_dt = _parse_contract_expiry(contract)
    if not expiry_dt:
        return []
    sym = symbol.upper()
    date_suffix = expiry_dt.strftime("%d%b%y").upper()
    bybit_sym = f"{sym}USDT-{date_suffix}"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
            klines_r, tk_r = await asyncio.gather(
                c.get("https://api.bybit.com/v5/market/kline",
                      params={"category": "linear", "symbol": bybit_sym,
                              "interval": "D", "limit": min(days, 200)}),
                c.get("https://api.bybit.com/v5/market/tickers",
                      params={"category": "linear", "symbol": bybit_sym}),
            )
            klines = klines_r.json().get("result", {}).get("list", [])
            tk = (tk_r.json().get("result", {}).get("list") or [{}])[0]
            ip_now = _safe_float(tk.get("indexPrice"))
            if not klines or not ip_now:
                return []
            result = []
            for k in klines:
                ts_ms = _safe_float(k[0])
                fp = _safe_float(k[4])
                if ts_ms is None or fp is None:
                    continue
                ts = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
                dte_hist = max(0, (expiry_dt - ts).days)
                busd = fp - ip_now
                result.append(BasisHistoryPoint(
                    timestamp=ts.isoformat(),
                    basis_usd=busd,
                    basis_pct_ann=_basis_ann(busd, ip_now, dte_hist),
                    futures_price=fp,
                    index_price=ip_now,
                    days_to_expiry=float(dte_hist),
                ))
            return sorted(result, key=lambda x: x.timestamp)
    except Exception:
        log.exception("bybit_history_error", contract=contract)
        return []


async def _cme_history(symbol: str, contract: str, days: int) -> list[BasisHistoryPoint]:
    if not _HAS_DERIBIT or not _AD_DERIVS_KEY or symbol.upper() != "BTC":
        return []
    expiry_dt = _parse_contract_expiry(contract)
    if not expiry_dt:
        return []
    full_code = f"BTCUSD_{expiry_dt.strftime('%y%m%d')}"
    try:
        async with httpx.AsyncClient(timeout=5.0) as hc:
            spot = await hc.get("https://api.binance.com/api/v3/ticker/price",
                                params={"symbol": "BTCUSDT"})
            ip_now = _safe_float(spot.json().get("price")) or 0.0

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
            resp = await c.get(
                f"https://api.amberdata.com/markets/futures/ohlcv/{full_code}",
                params={"exchange": "cme", "timeInterval": "days"},
                headers={"x-api-key": _AD_DERIVS_KEY, "accept": "application/json"},
            )
            if resp.status_code in (401, 403, 404):
                log.warning("cme_history_no_subscription", status=resp.status_code)
                return []
            resp.raise_for_status()
            data = resp.json().get("payload", {}).get("data", [])

        result = []
        for row in data:
            fp = _safe_float(row.get("close"))
            ts_str = row.get("exchangeTimestamp") or row.get("timestamp")
            if fp is None or not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
            except Exception:
                continue
            dte_hist = max(0, (expiry_dt - ts).days)
            busd = fp - ip_now
            result.append(BasisHistoryPoint(
                timestamp=ts.isoformat(),
                basis_usd=busd,
                basis_pct_ann=_basis_ann(busd, ip_now, dte_hist),
                futures_price=fp,
                index_price=ip_now,
                days_to_expiry=float(dte_hist),
            ))
        return sorted(result, key=lambda x: x.timestamp)[-days:]
    except Exception:
        log.exception("cme_history_error", contract=contract)
        return []


# ---- Public API --------------------------------------------------------------

async def get_basis_snapshot(symbol: str) -> list[BasisRow]:
    sym = symbol.upper()
    cached = _snap_cache_get(sym)
    if cached is not None:
        return cached

    results = await asyncio.gather(
        _fetch_deribit_snapshot(sym),
        _fetch_binance_snapshot(sym),
        _fetch_okx_snapshot(sym),
        _fetch_bybit_snapshot(sym),
        _fetch_cme_snapshot(sym),
        return_exceptions=True,
    )

    all_rows: list[BasisRow] = []
    for r in results:
        if isinstance(r, list):
            all_rows.extend(r)
        elif isinstance(r, Exception):
            log.warning("venue_snapshot_exception", error=str(r))

    all_rows.sort(key=lambda x: x.days_to_expiry)
    _snap_cache_set(sym, all_rows)
    return all_rows


async def get_basis_history(
    symbol: str, venue: str, contract: str, days: int = 89
) -> list[BasisHistoryPoint]:
    sym = symbol.upper()
    v = venue.lower()
    dispatch = {
        "deribit": _deribit_history,
        "binance": _binance_history,
        "okx": _okx_history,
        "bybit": _bybit_history,
        "cme": _cme_history,
    }
    fn = dispatch.get(v)
    if fn is None:
        return []
    return await fn(sym, contract, days)
