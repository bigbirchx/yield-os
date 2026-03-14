"""
Coinglass connector — secondary / cross-check source for funding rates.

Public endpoint:  GET https://open-api.coinglass.com/public/v2/funding
No auth required for the basic funding snapshot; COINGLASS_API_KEY is used
as the CG-API-KEY header for rate-limited / higher-tier endpoints.

Rate-field mapping from the API:
  uFundingRate    → Binance   (raw 8-hour rate)
  oFundingRate    → OKX       (raw 8-hour rate)
  bybitFundingRate→ Bybit     (raw 8-hour rate)
  dydxFundingRate → dYdX      (informational only, not in main snapshot)

All rates are annualized before returning (× 3 × 365).
"""
from __future__ import annotations

from dataclasses import dataclass

import structlog

import httpx

from app.core.config import settings

log = structlog.get_logger(__name__)

_BASE = settings.coinglass_api_url
_TIMEOUT = 8.0
_ANNUALIZE = 3 * 365  # 8-hour rate → APR


@dataclass
class CoinglassSnapshot:
    symbol: str
    binance_apr: float | None = None
    okx_apr: float | None = None
    bybit_apr: float | None = None
    dydx_apr: float | None = None
    fetched_at: str = ""


def _to_apr(raw: float | str | None) -> float | None:
    try:
        return float(raw) * _ANNUALIZE if raw is not None else None  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


async def fetch_funding_snapshot(symbol: str = "BTC") -> CoinglassSnapshot | None:
    """
    Fetch the latest funding rate snapshot from Coinglass for a single symbol.

    Returns None on any network or parsing error so callers can treat it as
    an optional cross-check rather than a hard dependency.
    """
    headers: dict[str, str] = {}
    if settings.coinglass_api_key:
        headers["CG-API-KEY"] = settings.coinglass_api_key

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{_BASE}/funding",
                headers=headers,
                params={"symbol": symbol.upper()},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        log.warning("coinglass_fetch_error", symbol=symbol, error=str(exc))
        return None

    # API may return {"code": "0", "data": [...]} or a list directly
    records = data.get("data", data) if isinstance(data, dict) else data
    if not isinstance(records, list) or not records:
        return None

    # Find the record matching the requested symbol
    target = None
    sym_upper = symbol.upper()
    for rec in records:
        if str(rec.get("symbol", "")).upper() == sym_upper:
            target = rec
            break
    if target is None and records:
        target = records[0]

    from datetime import UTC, datetime

    return CoinglassSnapshot(
        symbol=sym_upper,
        binance_apr=_to_apr(target.get("uFundingRate")),
        okx_apr=_to_apr(target.get("oFundingRate")),
        bybit_apr=_to_apr(target.get("bybitFundingRate")),
        dydx_apr=_to_apr(target.get("dydxFundingRate")),
        fetched_at=datetime.now(UTC).isoformat(),
    )
