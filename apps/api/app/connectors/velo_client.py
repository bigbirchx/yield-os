"""
Velo data connector.

Velo base URL: https://api.velo.xyz/v0
Endpoints used:
  GET /funding-rates       ?coin=BTC&venue=ALL      -> funding rate per venue
  GET /open-interest       ?coin=BTC&venue=ALL      -> OI per venue
  GET /market-summary      ?coin=BTC&venue=ALL      -> mark/index price, basis, volume

All responses are lists of venue-level objects. Unknown fields are preserved in
raw_payload for reconciliation.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from pydantic import BaseModel, Field
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = structlog.get_logger(__name__)

_DEFAULT_TIMEOUT = 15.0  # seconds
_RETRY_ATTEMPTS = 3
_BASE_URL = "https://api.velo.xyz/v0"


# ---------------------------------------------------------------------------
# Response shapes — maps Velo field names to our internal names.
# Fields not listed here are captured in raw_payload unchanged.
# ---------------------------------------------------------------------------


class VeloFundingRateRecord(BaseModel):
    """One row from GET /funding-rates."""

    venue: str
    coin: str
    funding_rate: float | None = None
    funding_rate_annualized: float | None = None
    timestamp: str | None = None

    model_config = {"extra": "allow"}


class VeloOpenInterestRecord(BaseModel):
    """One row from GET /open-interest."""

    venue: str
    coin: str
    open_interest_usd: float | None = None
    open_interest_contracts: float | None = None
    timestamp: str | None = None

    model_config = {"extra": "allow"}


class VeloMarketSummaryRecord(BaseModel):
    """One row from GET /market-summary — includes price and volume fields."""

    venue: str
    coin: str
    mark_price: float | None = None
    index_price: float | None = None
    basis_annualized: float | None = None
    spot_volume_usd: float | None = None
    perp_volume_usd: float | None = None
    timestamp: str | None = None

    model_config = {"extra": "allow"}


class VeloSnapshot(BaseModel):
    """Merged per-venue record for one coin across all three endpoints."""

    coin: str
    venue: str
    funding_rate: float | None = None
    open_interest_usd: float | None = None
    basis_annualized: float | None = None
    mark_price: float | None = None
    index_price: float | None = None
    spot_volume_usd: float | None = None
    perp_volume_usd: float | None = None
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    raw_funding: dict[str, Any] | None = None
    raw_oi: dict[str, Any] | None = None
    raw_summary: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


def _make_retry_decorator():
    return retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        stop=stop_after_attempt(_RETRY_ATTEMPTS),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )


class VeloClient:
    """
    Async HTTP client for the Velo API.

    Usage:
        async with VeloClient(api_key="...") as client:
            snapshots = await client.fetch_snapshots("BTC")
    """

    def __init__(self, api_key: str, base_url: str = _BASE_URL) -> None:
        if not api_key:
            raise ValueError("VELO_API_KEY is required")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "VeloClient":
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=_DEFAULT_TIMEOUT,
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @_make_retry_decorator()
    async def _get(self, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
        assert self._client is not None, "Use VeloClient as an async context manager"
        log.debug("velo_request", path=path, params=params)
        resp = await self._client.get(path, params=params)
        resp.raise_for_status()
        data = resp.json()
        # Velo returns either a list or {"data": [...]}
        if isinstance(data, list):
            return data
        return data.get("data", [])

    # ------------------------------------------------------------------
    # Public fetch methods
    # ------------------------------------------------------------------

    async def fetch_funding_rates(self, coin: str) -> list[VeloFundingRateRecord]:
        rows = await self._get("/funding-rates", {"coin": coin.upper(), "venue": "ALL"})
        return [VeloFundingRateRecord.model_validate(r) for r in rows]

    async def fetch_open_interest(self, coin: str) -> list[VeloOpenInterestRecord]:
        rows = await self._get("/open-interest", {"coin": coin.upper(), "venue": "ALL"})
        return [VeloOpenInterestRecord.model_validate(r) for r in rows]

    async def fetch_market_summary(self, coin: str) -> list[VeloMarketSummaryRecord]:
        rows = await self._get("/market-summary", {"coin": coin.upper(), "venue": "ALL"})
        return [VeloMarketSummaryRecord.model_validate(r) for r in rows]

    # ------------------------------------------------------------------
    # Merged snapshot: fires three requests concurrently, joins on venue
    # ------------------------------------------------------------------

    async def fetch_snapshots(self, coin: str) -> list[VeloSnapshot]:
        """
        Returns one VeloSnapshot per venue for the requested coin.
        Concurrent fetch of funding, OI, and summary endpoints.
        """
        coin_upper = coin.upper()
        log.info("velo_fetch_start", coin=coin_upper)

        try:
            funding_rows, oi_rows, summary_rows = await asyncio.gather(
                self.fetch_funding_rates(coin_upper),
                self.fetch_open_interest(coin_upper),
                self.fetch_market_summary(coin_upper),
            )
        except httpx.HTTPStatusError as exc:
            log.error(
                "velo_http_error",
                coin=coin_upper,
                status=exc.response.status_code,
                detail=exc.response.text[:200],
            )
            raise
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            log.error("velo_network_error", coin=coin_upper, error=str(exc))
            raise

        # Index secondary endpoints by venue for O(1) join
        oi_by_venue: dict[str, VeloOpenInterestRecord] = {
            r.venue: r for r in oi_rows
        }
        summary_by_venue: dict[str, VeloMarketSummaryRecord] = {
            r.venue: r for r in summary_rows
        }

        snapshots: list[VeloSnapshot] = []
        # Use funding rows as the primary iterator; venues without funding are skipped
        for fr in funding_rows:
            oi = oi_by_venue.get(fr.venue)
            sm = summary_by_venue.get(fr.venue)
            snapshots.append(
                VeloSnapshot(
                    coin=coin_upper,
                    venue=fr.venue,
                    funding_rate=fr.funding_rate,
                    open_interest_usd=oi.open_interest_usd if oi else None,
                    basis_annualized=sm.basis_annualized if sm else None,
                    mark_price=sm.mark_price if sm else None,
                    index_price=sm.index_price if sm else None,
                    spot_volume_usd=sm.spot_volume_usd if sm else None,
                    perp_volume_usd=sm.perp_volume_usd if sm else None,
                    raw_funding=fr.model_dump(),
                    raw_oi=oi.model_dump() if oi else None,
                    raw_summary=sm.model_dump() if sm else None,
                )
            )

        log.info("velo_fetch_done", coin=coin_upper, venues=len(snapshots))
        return snapshots
