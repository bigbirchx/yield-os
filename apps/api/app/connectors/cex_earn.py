"""
CeFi earn-product adapters.

Two :class:`ProtocolAdapter` subclasses:

  - :class:`BinanceEarnAdapter`  (Venue.BINANCE)
  - :class:`OkxEarnAdapter`      (Venue.OKX)

Data sources
------------
**Binance**

1. *Signed REST* (preferred): ``GET /sapi/v1/simple-earn/flexible/list`` and
   ``GET /sapi/v1/simple-earn/locked/list`` when ``settings.binance_api_key``
   and ``settings.binance_api_secret`` are both set.  HMAC-SHA256 signed per
   Binance API conventions.

2. *DeFiLlama fallback*: ``GET https://yields.llama.fi/pools`` filtered to
   ``project == "binance"``.  Requires no credentials but may lag the live
   exchange rates by up to one hour and covers fewer assets.

**OKX**

1. *Public REST* (preferred): ``GET /api/v5/finance/savings/lending-rate-summary``
   — no authentication required.  ``avgRate`` is a daily rate; annualised as
   ``avgRate × 365 × 100``.

2. *DeFiLlama fallback*: same pool endpoint filtered to ``project == "okx"``.

Opportunity structure
---------------------
All earn products emit SUPPLY-side opportunities:

  - ``opportunity_type = CEX_EARN``
  - ``side            = SUPPLY``
  - Flexible products: ``effective_duration = OVERNIGHT``
  - Locked products:   ``effective_duration = FIXED_TERM``,
                       ``days_to_maturity = product term in days``
  - ``chain`` uses the exchange name as a pseudo-chain (``"BINANCE"`` / ``"OKX"``)

Refresh: 900 seconds (15 minutes).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac as _hmac_mod
import time
import urllib.parse
from typing import Any

import structlog

from app.connectors.base_adapter import ProtocolAdapter
from app.connectors.defillama_client import DeFiLlamaClient, DeFiLlamaPool
from app.connectors.http_client import get_json
from app.core.config import settings
from asset_registry import Chain, Venue
from opportunity_schema import (
    EffectiveDuration,
    LiquidityInfo,
    MarketOpportunity,
    OpportunitySide,
    OpportunityType,
    RewardBreakdown,
    RewardType,
)

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BINANCE_BASE = "https://api.binance.com"
_OKX_BASE = "https://www.okx.com/api/v5"

# DeFiLlama project slugs — filter by these when using the fallback source
_BINANCE_DFL_PROJECT = "binance"
_OKX_DFL_PROJECT = "okx"

# Minimum meaningful APY to include; filters out stale / zero-rate entries
_MIN_APY_PCT = 0.01


# ---------------------------------------------------------------------------
# Binance HMAC signing
# ---------------------------------------------------------------------------

def _binance_sign(params: dict[str, Any], secret: str) -> dict[str, Any]:
    """Append a timestamp and HMAC-SHA256 signature to a Binance params dict."""
    ts = int(time.time() * 1000)
    signed: dict[str, Any] = {**params, "timestamp": ts}
    query_string = urllib.parse.urlencode(signed)
    sig = _hmac_mod.new(
        secret.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    signed["signature"] = sig
    return signed


# ---------------------------------------------------------------------------
# DeFiLlama pool → MarketOpportunity helper
# ---------------------------------------------------------------------------

def _pool_to_effective_duration(pool: DeFiLlamaPool) -> EffectiveDuration:
    """Infer duration from DeFiLlama pool metadata."""
    meta = (pool.pool_meta or "").lower()
    if any(k in meta for k in ("fixed", "lock", "locked", "term", "days")):
        return EffectiveDuration.FIXED_TERM
    return EffectiveDuration.OVERNIGHT


# ═══════════════════════════════════════════════════════════════════════════
# Binance Earn
# ═══════════════════════════════════════════════════════════════════════════


class BinanceEarnAdapter(ProtocolAdapter):
    """Binance Simple Earn adapter (flexible + locked products).

    Uses the signed Binance API when credentials are configured, or falls back
    to DeFiLlama pool data for Binance earn products.
    """

    # -- ProtocolAdapter properties ------------------------------------------

    @property
    def venue(self) -> Venue:
        return Venue.BINANCE

    @property
    def protocol_name(self) -> str:
        return "Binance Simple Earn"

    @property
    def protocol_slug(self) -> str:
        return "binance-earn"

    @property
    def supported_chains(self) -> list[Chain]:
        return []  # CeFi — no on-chain deployment

    @property
    def refresh_interval_seconds(self) -> int:
        return 900

    @property
    def requires_api_key(self) -> bool:
        return True  # Preferred path uses signed endpoint; DeFiLlama fallback is keyless

    @property
    def api_key_env_var(self) -> str | None:
        return "BINANCE_API_KEY"

    # -- Binance signed-API helpers ------------------------------------------

    @property
    def _has_credentials(self) -> bool:
        return bool(
            getattr(settings, "binance_api_key", "")
            and getattr(settings, "binance_api_secret", "")
        )

    async def _fetch_flexible_signed(self) -> list[dict]:
        """Fetch Binance Simple Earn flexible products via signed REST."""
        api_key = settings.binance_api_key  # type: ignore[attr-defined]
        api_secret = settings.binance_api_secret  # type: ignore[attr-defined]
        params = _binance_sign({"current": 1, "size": 100}, api_secret)
        try:
            data = await get_json(
                f"{_BINANCE_BASE}/sapi/v1/simple-earn/flexible/list",
                params=params,
                headers={"X-MBX-APIKEY": api_key},
            )
            return data.get("data", {}).get("rows", [])
        except Exception as exc:
            log.warning("binance_earn_flexible_error", error=str(exc))
            return []

    async def _fetch_locked_signed(self) -> list[dict]:
        """Fetch Binance Simple Earn locked products via signed REST."""
        api_key = settings.binance_api_key  # type: ignore[attr-defined]
        api_secret = settings.binance_api_secret  # type: ignore[attr-defined]
        params = _binance_sign({"current": 1, "size": 100}, api_secret)
        try:
            data = await get_json(
                f"{_BINANCE_BASE}/sapi/v1/simple-earn/locked/list",
                params=params,
                headers={"X-MBX-APIKEY": api_key},
            )
            return data.get("data", {}).get("rows", [])
        except Exception as exc:
            log.warning("binance_earn_locked_error", error=str(exc))
            return []

    # -- Signed-API opportunity builders ------------------------------------

    def _flexible_row_to_opp(self, row: dict) -> MarketOpportunity | None:
        """Convert one Binance flexible earn row into a MarketOpportunity."""
        asset_raw = row.get("asset", "")
        if not asset_raw:
            return None
        apy_raw = row.get("latestAnnualPercentageRate")
        if apy_raw is None:
            return None
        try:
            apy_pct = float(apy_raw) * 100.0
        except (TypeError, ValueError):
            return None
        if apy_pct < _MIN_APY_PCT:
            return None

        canonical = self.normalize_symbol(asset_raw)
        tvl = row.get("totalPersonalQuota")
        try:
            tvl_usd = float(tvl) if tvl is not None else None
        except (TypeError, ValueError):
            tvl_usd = None

        return self.build_opportunity(
            asset_id=canonical,
            asset_symbol=asset_raw.upper(),
            chain="BINANCE",
            market_id=f"binance-earn-flex-{asset_raw.lower()}",
            market_name=f"Binance Flexible Earn {asset_raw.upper()}",
            side=OpportunitySide.SUPPLY,
            opportunity_type=OpportunityType.CEX_EARN,
            effective_duration=EffectiveDuration.OVERNIGHT,
            total_apy_pct=apy_pct,
            base_apy_pct=apy_pct,
            reward_breakdown=[
                RewardBreakdown(
                    reward_type=RewardType.NATIVE_YIELD,
                    apy_pct=apy_pct,
                    is_variable=True,
                    notes="Binance Simple Earn flexible savings rate",
                ),
            ],
            tvl_usd=tvl_usd,
            liquidity=LiquidityInfo(),
            tags=["cex-earn", "flexible", "binance"],
            source_url=f"https://www.binance.com/en/earn",
        )

    def _locked_row_to_opp(self, row: dict) -> MarketOpportunity | None:
        """Convert one Binance locked earn row into a MarketOpportunity."""
        asset_raw = row.get("asset", "")
        if not asset_raw:
            return None
        duration_days = row.get("duration")
        if duration_days is None:
            return None
        try:
            days = int(duration_days)
        except (TypeError, ValueError):
            return None

        apy_raw = row.get("latestAnnualPercentageRate")
        if apy_raw is None:
            return None
        try:
            apy_pct = float(apy_raw) * 100.0
        except (TypeError, ValueError):
            return None
        if apy_pct < _MIN_APY_PCT:
            return None

        canonical = self.normalize_symbol(asset_raw)
        project_id = row.get("projectId", f"{asset_raw}{days}D")

        return self.build_opportunity(
            asset_id=canonical,
            asset_symbol=asset_raw.upper(),
            chain="BINANCE",
            market_id=f"binance-earn-locked-{project_id.lower()}",
            market_name=f"Binance Locked Earn {asset_raw.upper()} {days}D",
            side=OpportunitySide.SUPPLY,
            opportunity_type=OpportunityType.CEX_EARN,
            effective_duration=EffectiveDuration.FIXED_TERM,
            days_to_maturity=float(days),
            total_apy_pct=apy_pct,
            base_apy_pct=apy_pct,
            reward_breakdown=[
                RewardBreakdown(
                    reward_type=RewardType.NATIVE_YIELD,
                    apy_pct=apy_pct,
                    is_variable=False,
                    notes=f"Binance Simple Earn locked savings {days}-day rate",
                ),
            ],
            liquidity=LiquidityInfo(
                has_lockup=True,
                lockup_days=float(days),
            ),
            tags=["cex-earn", "locked", "binance", f"{days}d"],
            source_url="https://www.binance.com/en/earn",
        )

    # -- DeFiLlama fallback --------------------------------------------------

    async def _fetch_via_defillama(self) -> list[MarketOpportunity]:
        """Fetch Binance earn rates from DeFiLlama yields pools."""
        try:
            async with DeFiLlamaClient(api_key=settings.defillama_api_key) as client:
                pools = await client.fetch_pools()
        except Exception as exc:
            log.warning("binance_earn_defillama_error", error=str(exc))
            return []

        opps: list[MarketOpportunity] = []
        for pool in pools:
            if pool.project.lower() != _BINANCE_DFL_PROJECT:
                continue
            opp = self._defillama_pool_to_opp(pool)
            if opp is not None:
                opps.append(opp)
        return opps

    def _defillama_pool_to_opp(self, pool: DeFiLlamaPool) -> MarketOpportunity | None:
        """Convert a DeFiLlama Binance pool into a MarketOpportunity."""
        if pool.apy is None or pool.apy < _MIN_APY_PCT:
            return None
        symbol_raw = pool.symbol.upper().split("-")[0]  # strip "USDC-FLEX" → "USDC"
        canonical = self.normalize_symbol(symbol_raw)
        duration = _pool_to_effective_duration(pool)

        return self.build_opportunity(
            asset_id=canonical,
            asset_symbol=symbol_raw,
            chain="BINANCE",
            market_id=f"binance-earn-dfl-{pool.pool}",
            market_name=f"Binance Earn {symbol_raw}" + (f" ({pool.pool_meta})" if pool.pool_meta else ""),
            side=OpportunitySide.SUPPLY,
            opportunity_type=OpportunityType.CEX_EARN,
            effective_duration=duration,
            total_apy_pct=pool.apy,
            base_apy_pct=pool.apy_base or pool.apy,
            reward_breakdown=[
                RewardBreakdown(
                    reward_type=RewardType.NATIVE_YIELD,
                    apy_pct=pool.apy,
                    is_variable=(duration == EffectiveDuration.OVERNIGHT),
                    notes="Binance earn rate via DeFiLlama",
                ),
            ],
            tvl_usd=pool.tvl_usd,
            liquidity=LiquidityInfo(
                available_liquidity_usd=pool.tvl_usd,
                has_lockup=(duration == EffectiveDuration.FIXED_TERM),
            ),
            tags=["cex-earn", "binance", "defillama"],
            source_url="https://www.binance.com/en/earn",
        )

    # -- ProtocolAdapter interface -------------------------------------------

    async def fetch_opportunities(
        self,
        symbols: list[str] | None = None,
        chains: list[Chain] | None = None,
    ) -> list[MarketOpportunity]:
        """Fetch Binance earn products.

        Uses the signed Binance API when credentials are configured in
        ``settings.binance_api_key`` / ``settings.binance_api_secret``,
        otherwise falls back to DeFiLlama.
        """
        if self._has_credentials:
            flex_rows, locked_rows = await asyncio.gather(
                self._fetch_flexible_signed(),
                self._fetch_locked_signed(),
            )
            opps: list[MarketOpportunity] = []
            for row in flex_rows:
                try:
                    opp = self._flexible_row_to_opp(row)
                    if opp is not None:
                        opps.append(opp)
                except Exception as exc:
                    log.warning("binance_earn_flex_row_error", asset=row.get("asset"), error=str(exc))
            for row in locked_rows:
                try:
                    opp = self._locked_row_to_opp(row)
                    if opp is not None:
                        opps.append(opp)
                except Exception as exc:
                    log.warning("binance_earn_locked_row_error", asset=row.get("asset"), error=str(exc))
        else:
            log.info(
                "binance_earn_using_defillama_fallback",
                reason="binance_api_key or binance_api_secret not set",
            )
            opps = await self._fetch_via_defillama()

        # Symbol filter
        if symbols:
            opps = [o for o in opps if o.asset_id in symbols]

        log.info("binance_earn_fetch_done", opportunities=len(opps))
        return opps

    async def health_check(self) -> dict[str, Any]:
        try:
            if self._has_credentials:
                rows = await self._fetch_flexible_signed()
                ok = len(rows) > 0
            else:
                # Health check via a lightweight DeFiLlama probe
                data = await get_json("https://yields.llama.fi/pools")
                ok = isinstance(data, dict) and "data" in data
            return {"status": "ok" if ok else "degraded", "last_success": self._last_success, "error": None}
        except Exception as exc:
            return {"status": "down", "last_success": self._last_success, "error": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════
# OKX Earn
# ═══════════════════════════════════════════════════════════════════════════


class OkxEarnAdapter(ProtocolAdapter):
    """OKX earn-product adapter (savings / flexible earn).

    Primary source: OKX public savings lending-rate-summary endpoint.
    Fallback: DeFiLlama yield pools filtered to ``project == "okx"``.

    The OKX savings ``avgRate`` is a *daily* rate; we annualise it as:
      ``apy_pct = avgRate × 365 × 100``
    """

    # -- ProtocolAdapter properties ------------------------------------------

    @property
    def venue(self) -> Venue:
        return Venue.OKX

    @property
    def protocol_name(self) -> str:
        return "OKX Earn"

    @property
    def protocol_slug(self) -> str:
        return "okx-earn"

    @property
    def supported_chains(self) -> list[Chain]:
        return []

    @property
    def refresh_interval_seconds(self) -> int:
        return 900

    @property
    def requires_api_key(self) -> bool:
        return False  # Public endpoint used

    @property
    def api_key_env_var(self) -> str | None:
        return None

    # -- OKX public savings endpoint ----------------------------------------

    async def _fetch_okx_savings_rates(self) -> list[dict]:
        """Fetch OKX lending rate summary (public endpoint)."""
        try:
            data = await get_json(f"{_OKX_BASE}/finance/savings/lending-rate-summary")
            if data.get("code") != "0":
                log.warning(
                    "okx_earn_api_non_zero_code",
                    code=data.get("code"),
                    msg=data.get("msg"),
                )
                return []
            return data.get("data") or []
        except Exception as exc:
            log.warning("okx_earn_savings_api_error", error=str(exc))
            return []

    def _savings_row_to_opp(self, row: dict) -> MarketOpportunity | None:
        """Convert one OKX savings lending-rate-summary row into a MarketOpportunity."""
        ccy = row.get("ccy", "")
        if not ccy:
            return None
        avg_rate = row.get("avgRate")
        if avg_rate is None:
            return None
        try:
            # avgRate is a daily rate decimal (e.g. 0.000055 = 0.0055%/day)
            apy_pct = float(avg_rate) * 365.0 * 100.0
        except (TypeError, ValueError):
            return None
        if apy_pct < _MIN_APY_PCT:
            return None

        canonical = self.normalize_symbol(ccy)
        avg_amt_usd_raw = row.get("avgAmtUsd")
        try:
            tvl_usd = float(avg_amt_usd_raw) if avg_amt_usd_raw is not None else None
        except (TypeError, ValueError):
            tvl_usd = None

        return self.build_opportunity(
            asset_id=canonical,
            asset_symbol=ccy.upper(),
            chain="OKX",
            market_id=f"okx-earn-savings-{ccy.lower()}",
            market_name=f"OKX Savings {ccy.upper()}",
            side=OpportunitySide.SUPPLY,
            opportunity_type=OpportunityType.CEX_EARN,
            effective_duration=EffectiveDuration.OVERNIGHT,
            total_apy_pct=apy_pct,
            base_apy_pct=apy_pct,
            reward_breakdown=[
                RewardBreakdown(
                    reward_type=RewardType.NATIVE_YIELD,
                    apy_pct=apy_pct,
                    is_variable=True,
                    notes="OKX savings avg lending rate (daily rate × 365)",
                ),
            ],
            tvl_usd=tvl_usd,
            liquidity=LiquidityInfo(available_liquidity_usd=tvl_usd),
            tags=["cex-earn", "flexible", "okx"],
            source_url=f"https://www.okx.com/earn",
        )

    # -- DeFiLlama fallback --------------------------------------------------

    async def _fetch_via_defillama(self) -> list[MarketOpportunity]:
        """Fetch OKX earn rates from DeFiLlama yields pools."""
        try:
            async with DeFiLlamaClient(api_key=settings.defillama_api_key) as client:
                pools = await client.fetch_pools()
        except Exception as exc:
            log.warning("okx_earn_defillama_error", error=str(exc))
            return []

        opps: list[MarketOpportunity] = []
        for pool in pools:
            if pool.project.lower() != _OKX_DFL_PROJECT:
                continue
            opp = self._defillama_pool_to_opp(pool)
            if opp is not None:
                opps.append(opp)
        return opps

    def _defillama_pool_to_opp(self, pool: DeFiLlamaPool) -> MarketOpportunity | None:
        """Convert a DeFiLlama OKX pool into a MarketOpportunity."""
        if pool.apy is None or pool.apy < _MIN_APY_PCT:
            return None
        symbol_raw = pool.symbol.upper().split("-")[0]
        canonical = self.normalize_symbol(symbol_raw)
        duration = _pool_to_effective_duration(pool)

        return self.build_opportunity(
            asset_id=canonical,
            asset_symbol=symbol_raw,
            chain="OKX",
            market_id=f"okx-earn-dfl-{pool.pool}",
            market_name=f"OKX Earn {symbol_raw}" + (f" ({pool.pool_meta})" if pool.pool_meta else ""),
            side=OpportunitySide.SUPPLY,
            opportunity_type=OpportunityType.CEX_EARN,
            effective_duration=duration,
            total_apy_pct=pool.apy,
            base_apy_pct=pool.apy_base or pool.apy,
            reward_breakdown=[
                RewardBreakdown(
                    reward_type=RewardType.NATIVE_YIELD,
                    apy_pct=pool.apy,
                    is_variable=(duration == EffectiveDuration.OVERNIGHT),
                    notes="OKX earn rate via DeFiLlama",
                ),
            ],
            tvl_usd=pool.tvl_usd,
            liquidity=LiquidityInfo(
                available_liquidity_usd=pool.tvl_usd,
                has_lockup=(duration == EffectiveDuration.FIXED_TERM),
            ),
            tags=["cex-earn", "okx", "defillama"],
            source_url="https://www.okx.com/earn",
        )

    # -- ProtocolAdapter interface -------------------------------------------

    async def fetch_opportunities(
        self,
        symbols: list[str] | None = None,
        chains: list[Chain] | None = None,
    ) -> list[MarketOpportunity]:
        """Fetch OKX earn products.

        Tries the public OKX savings lending-rate-summary endpoint first;
        falls back to DeFiLlama if that endpoint returns no data.
        """
        rows = await self._fetch_okx_savings_rates()

        if rows:
            opps: list[MarketOpportunity] = []
            for row in rows:
                try:
                    opp = self._savings_row_to_opp(row)
                    if opp is not None:
                        opps.append(opp)
                except Exception as exc:
                    log.warning("okx_earn_row_error", ccy=row.get("ccy"), error=str(exc))
        else:
            log.info("okx_earn_using_defillama_fallback", reason="public api returned no data")
            opps = await self._fetch_via_defillama()

        if symbols:
            opps = [o for o in opps if o.asset_id in symbols]

        log.info("okx_earn_fetch_done", opportunities=len(opps))
        return opps

    async def health_check(self) -> dict[str, Any]:
        try:
            data = await get_json(f"{_OKX_BASE}/finance/savings/lending-rate-summary")
            ok = data.get("code") == "0" and bool(data.get("data"))
            return {"status": "ok" if ok else "degraded", "last_success": self._last_success, "error": None}
        except Exception as exc:
            return {"status": "down", "last_success": self._last_success, "error": str(exc)}
