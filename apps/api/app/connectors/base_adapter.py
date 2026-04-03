"""
Abstract base class for protocol adapters and the adapter registry.

Every protocol connector that emits :class:`MarketOpportunity` instances
must subclass :class:`ProtocolAdapter`.  The base class provides:

  - Symbol normalisation and asset classification from the asset-registry
  - A :meth:`build_opportunity` factory that auto-fills venue / protocol /
    timestamp fields so subclasses only supply market-specific data
  - AMM-LP and Pendle detection helpers
  - Error handling, retry, and health tracking

Usage::

    class AaveV3Adapter(ProtocolAdapter):
        venue = Venue.AAVE_V3
        protocol_name = "Aave V3"
        ...

        async def fetch_opportunities(self, symbols=None, chains=None):
            reserves = await self._fetch_reserves(chains)
            return [self.build_opportunity(...) for r in reserves]
"""
from __future__ import annotations

import abc
import asyncio
import time
from datetime import UTC, datetime
from typing import Any

import structlog

from asset_registry import (
    ASSET_REGISTRY,
    AssetNormalizer,
    Chain,
    Venue,
)
from opportunity_schema import (
    MarketOpportunity,
    OpportunitySide,
    generate_opportunity_id,
)

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Retry / error-handling constants
# ---------------------------------------------------------------------------

_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 1.0  # 1 s, 2 s, 4 s

# Case-insensitive lookup into the asset registry
_UPPER_MAP: dict[str, str] = {k.upper(): k for k in ASSET_REGISTRY}


# ═══════════════════════════════════════════════════════════════════════════
# Abstract adapter
# ═══════════════════════════════════════════════════════════════════════════


class ProtocolAdapter(abc.ABC):
    """Base class every protocol connector must subclass.

    Subclasses **must** implement:
      - :meth:`fetch_opportunities`
      - :meth:`health_check`

    And declare the class-level properties as plain attributes or
    ``@property`` overrides.
    """

    # -- Properties that subclasses must set ----------------------------------

    @property
    @abc.abstractmethod
    def venue(self) -> Venue: ...

    @property
    @abc.abstractmethod
    def protocol_name(self) -> str: ...

    @property
    @abc.abstractmethod
    def protocol_slug(self) -> str: ...

    @property
    @abc.abstractmethod
    def supported_chains(self) -> list[Chain]: ...

    @property
    @abc.abstractmethod
    def refresh_interval_seconds(self) -> int: ...

    @property
    @abc.abstractmethod
    def requires_api_key(self) -> bool: ...

    @property
    @abc.abstractmethod
    def api_key_env_var(self) -> str | None: ...

    # -- Abstract methods -----------------------------------------------------

    @abc.abstractmethod
    async def fetch_opportunities(
        self,
        symbols: list[str] | None = None,
        chains: list[Chain] | None = None,
    ) -> list[MarketOpportunity]:
        """Fetch current opportunities from the upstream source.

        Parameters
        ----------
        symbols:
            If provided, limit to these canonical asset IDs.
            ``None`` means fetch all tracked assets.
        chains:
            If provided, limit to these chains.
            ``None`` means all :attr:`supported_chains`.
        """
        ...

    @abc.abstractmethod
    async def health_check(self) -> dict[str, Any]:
        """Lightweight probe to verify the upstream is reachable.

        Must return::

            {"status": "ok"|"degraded"|"down",
             "last_success": datetime | None,
             "error": str | None}
        """
        ...

    # -- Health tracking state ------------------------------------------------

    def __init__(self) -> None:
        self._normalizer = AssetNormalizer()
        self._last_success: datetime | None = None
        self._last_fetch: float = 0.0  # monotonic timestamp
        self._last_error: str | None = None

    # -- Concrete convenience methods -----------------------------------------

    def normalize_symbol(
        self,
        raw_symbol: str,
        chain: Chain | None = None,
    ) -> str:
        """Resolve a venue-specific symbol to its canonical ID.

        Returns the raw symbol unchanged if no mapping exists.
        """
        return self._normalizer.normalize_or_passthrough(
            self.venue, raw_symbol, chain=chain,
        )

    def classify_asset(
        self,
        canonical_id: str,
    ) -> tuple[str, str, str]:
        """Look up ``(umbrella, sub_type, fungibility)`` from the taxonomy.

        Checks the static registry first, then the dynamic token universe,
        and falls back to ``("OTHER", "NATIVE_TOKEN", "RELATED")`` for truly
        unknown assets.
        """
        resolved = _UPPER_MAP.get(canonical_id.upper(), canonical_id)
        asset = ASSET_REGISTRY.get(resolved)
        if asset is not None:
            return (
                asset.umbrella.value,
                asset.sub_type.value,
                asset.fungibility.value,
            )

        # Check dynamic token universe
        try:
            from app.services.token_universe import get_token_universe

            universe = get_token_universe()
            dynamic = universe.get_token(canonical_id)
            if dynamic is not None:
                return (
                    dynamic.umbrella.value,
                    dynamic.sub_type.value,
                    dynamic.fungibility.value,
                )
        except Exception:
            pass

        return ("OTHER", "NATIVE_TOKEN", "RELATED")

    def build_opportunity(self, **kwargs: Any) -> MarketOpportunity:
        """Factory that pre-fills adapter-level fields.

        Callers provide market-specific fields; this method injects:
          - ``venue``, ``protocol``, ``protocol_slug``, ``data_source``
          - ``last_updated_at``, ``data_freshness_seconds``
          - ``opportunity_id`` (generated if not supplied)
          - ``umbrella_group`` and ``asset_sub_type`` (looked up if missing)

        ``kwargs`` are forwarded to the :class:`MarketOpportunity` constructor,
        so any auto-filled value can be overridden explicitly.
        """
        now = datetime.now(UTC)

        # Auto-fill identity fields
        defaults: dict[str, Any] = {
            "venue": self.venue.value,
            "protocol": self.protocol_name,
            "protocol_slug": self.protocol_slug,
            "data_source": self.protocol_slug,
            "last_updated_at": now,
            "data_freshness_seconds": 0,
        }

        # Auto-classify asset if umbrella_group / asset_sub_type not given
        asset_id = kwargs.get("asset_id", "")
        if "umbrella_group" not in kwargs or "asset_sub_type" not in kwargs:
            umbrella, sub_type, _ = self.classify_asset(asset_id)
            defaults.setdefault("umbrella_group", umbrella)
            defaults.setdefault("asset_sub_type", sub_type)

        # Auto-generate opportunity_id if not supplied
        if "opportunity_id" not in kwargs:
            defaults["opportunity_id"] = generate_opportunity_id(
                venue=self.venue.value,
                chain=kwargs.get("chain", ""),
                protocol=self.protocol_slug,
                market_id=kwargs.get("market_id", ""),
                side=kwargs.get("side", OpportunitySide.SUPPLY),
            )

        # kwargs override defaults
        merged = {**defaults, **kwargs}
        return MarketOpportunity(**merged)

    def detect_and_skip_amm_lp(self, raw_symbol: str) -> bool:
        """Return ``True`` if *raw_symbol* looks like an AMM LP token."""
        return self._normalizer.is_amm_lp(self.venue, raw_symbol)

    def detect_pendle(self, raw_symbol: str) -> tuple[bool, str | None]:
        """Return ``(is_pendle, pendle_type)`` — ``"PT"`` or ``"YT"``."""
        return self._normalizer.is_pendle(self.venue, raw_symbol)

    # -- Safe fetch wrapper ---------------------------------------------------

    async def safe_fetch_opportunities(
        self,
        symbols: list[str] | None = None,
        chains: list[Chain] | None = None,
    ) -> list[MarketOpportunity]:
        """Call :meth:`fetch_opportunities` with retry + error isolation.

        On total failure, logs the error, updates health state, and returns
        an empty list so the ingestion pipeline continues.
        """
        last_exc: Exception | None = None

        for attempt in range(_RETRY_ATTEMPTS):
            try:
                result = await self.fetch_opportunities(
                    symbols=symbols, chains=chains,
                )
                self._last_success = datetime.now(UTC)
                self._last_fetch = time.monotonic()
                self._last_error = None
                log.info(
                    "adapter_fetch_ok",
                    adapter=self.protocol_slug,
                    opportunities=len(result),
                )
                return result

            except Exception as exc:
                last_exc = exc
                delay = _RETRY_BASE_DELAY * (2 ** attempt)
                log.warning(
                    "adapter_fetch_retry",
                    adapter=self.protocol_slug,
                    attempt=attempt + 1,
                    error=str(exc),
                    retry_in=delay,
                )
                if attempt < _RETRY_ATTEMPTS - 1:
                    await asyncio.sleep(delay)

        # All retries exhausted
        self._last_error = str(last_exc)
        log.exception(
            "adapter_fetch_failed",
            adapter=self.protocol_slug,
            error=str(last_exc),
        )
        return []

    # -- Refresh bookkeeping --------------------------------------------------

    @property
    def seconds_since_last_fetch(self) -> float:
        """Seconds since the last successful fetch (monotonic)."""
        if self._last_fetch == 0.0:
            return float("inf")
        return time.monotonic() - self._last_fetch

    @property
    def is_due_for_refresh(self) -> bool:
        """True if this adapter should be called again."""
        return self.seconds_since_last_fetch >= self.refresh_interval_seconds


# ═══════════════════════════════════════════════════════════════════════════
# Adapter registry
# ═══════════════════════════════════════════════════════════════════════════


class AdapterRegistry:
    """Central registry of all :class:`ProtocolAdapter` instances.

    The scheduler or startup code registers each adapter once; the
    ingestion loop queries :meth:`get_due_for_refresh` every tick.
    """

    def __init__(self) -> None:
        self._adapters: dict[str, ProtocolAdapter] = {}

    def register(self, adapter: ProtocolAdapter) -> None:
        """Register an adapter.  Keyed by ``venue.value``."""
        key = adapter.venue.value
        self._adapters[key] = adapter
        log.info(
            "adapter_registered",
            venue=key,
            protocol=adapter.protocol_slug,
            chains=[c.value for c in adapter.supported_chains],
            refresh_seconds=adapter.refresh_interval_seconds,
        )

    def get_all(self) -> list[ProtocolAdapter]:
        """Return all registered adapters."""
        return list(self._adapters.values())

    def get_by_venue(self, venue: Venue) -> ProtocolAdapter | None:
        """Look up an adapter by its venue enum."""
        return self._adapters.get(venue.value)

    def get_due_for_refresh(self) -> list[ProtocolAdapter]:
        """Return adapters whose last fetch is older than their interval."""
        return [a for a in self._adapters.values() if a.is_due_for_refresh]
