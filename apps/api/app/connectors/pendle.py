"""
Pendle Finance adapter.

Architecture
────────────
Pendle splits yield-bearing assets into two token types per *market*:

  PT (Principal Token)
    Fixed yield to maturity.  The PT trades at a discount to par (1.0) and
    redeems at par at expiry.  This is a zero-coupon bond in DeFi form.
    The implied fixed APY = annualised discount rate to par.

  YT (Yield Token)
    Leveraged exposure to the variable yield until maturity.  A YT holder
    receives all floating yield accrued by the underlying until expiry.
    The YT floating APY = implied annualised yield, can be negative if the
    market prices in declining underlying rates.

Each market wraps a single *underlying* yield-bearing asset (SY wrapper),
e.g. stETH, sUSDe, weETH.  We surface the underlying symbol as the
canonical ``asset_id`` so PT/YT rows align with the lending/staking rows
for the same asset.

Data source
───────────
Pendle public REST API — no auth required.

  Endpoint: GET {pendle_api_url}/v1/{chainId}/markets
  Pagination: ?limit=100&skip=N&isExpired=false
  Key fields:
    impliedApy        — PT fixed APY (decimal fraction, e.g. 0.053 = 5.3%)
    ytFloatingApy     — YT variable APY (decimal fraction, can be negative)
    underlyingApy     — Underlying asset's current yield (decimal fraction)
    expiry            — ISO8601 maturity datetime
    liquidity.usd     — Total AMM liquidity in USD
    isActive          — False when market is winding down / just expired
    underlyingAsset   — {symbol, address, ...} of the yield source
    pt / yt           — token metadata (symbol, price, decimals)

APY encoding
────────────
All APY fields are decimal fractions (0.053 = 5.3%).  Multiply ×100.

Supported chains: ETHEREUM (1), ARBITRUM (42161), BSC (56).
Refresh interval: 600 s.
Minimum market liquidity: $100 000 (skip zombie markets).
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog

from app.connectors.base_adapter import ProtocolAdapter
from app.connectors.http_client import get_json
from app.core.config import settings
from asset_registry import Chain, Venue
from opportunity_schema import (
    EffectiveDuration,
    LiquidityInfo,
    MarketOpportunity,
    OpportunitySide,
    OpportunityType,
    ReceiptTokenInfo,
    RewardBreakdown,
    RewardType,
)

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Chain mapping
# ---------------------------------------------------------------------------

_CHAIN_ID_TO_ENUM: dict[int, Chain] = {
    1: Chain.ETHEREUM,
    42161: Chain.ARBITRUM,
    56: Chain.BSC,
}

_CHAIN_ENUM_TO_ID: dict[Chain, int] = {v: k for k, v in _CHAIN_ID_TO_ENUM.items()}

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PAGE_SIZE = 100
_MIN_LIQUIDITY_USD = 100_000.0
_SECONDS_PER_YEAR = 365.25 * 86400


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class PendleAdapter(ProtocolAdapter):
    """Pendle Finance PT + YT adapter — Ethereum, Arbitrum, BSC."""

    @property
    def venue(self) -> Venue:
        return Venue.PENDLE

    @property
    def protocol_name(self) -> str:
        return "Pendle"

    @property
    def protocol_slug(self) -> str:
        return "pendle"

    @property
    def supported_chains(self) -> list[Chain]:
        return [Chain.ETHEREUM, Chain.ARBITRUM, Chain.BSC]

    @property
    def refresh_interval_seconds(self) -> int:
        return 600

    @property
    def requires_api_key(self) -> bool:
        return False

    @property
    def api_key_env_var(self) -> str | None:
        return None

    # -- Fetch -----------------------------------------------------------------

    async def fetch_opportunities(
        self,
        symbols: list[str] | None = None,
        chains: list[Chain] | None = None,
    ) -> list[MarketOpportunity]:
        effective_chains = [
            c for c in (chains or self.supported_chains)
            if c in _CHAIN_ENUM_TO_ID
        ]

        # Fetch all chains concurrently
        tasks = [self._fetch_chain(c, symbols) for c in effective_chains]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_opps: list[MarketOpportunity] = []
        for chain, result in zip(effective_chains, results):
            if isinstance(result, Exception):
                log.warning("pendle_chain_error", chain=chain.value, error=str(result))
            else:
                all_opps.extend(result)

        log.info(
            "pendle_fetch_done",
            total=len(all_opps),
            chains=[c.value for c in effective_chains],
        )
        return all_opps

    # -- Per-chain fetch with pagination ---------------------------------------

    async def _fetch_chain(
        self,
        chain: Chain,
        symbols: list[str] | None,
    ) -> list[MarketOpportunity]:
        chain_id = _CHAIN_ENUM_TO_ID[chain]
        base = f"{settings.pendle_api_url}/v1/{chain_id}/markets"

        # Paginate: fetch pages until skip >= total or empty page
        markets: list[dict] = []
        skip = 0
        total: int | None = None

        while True:
            page = await get_json(
                base,
                params={
                    "limit": _PAGE_SIZE,
                    "skip": skip,
                    "isExpired": "false",
                },
            )
            page_results: list[dict] = page.get("results", [])
            if total is None:
                total = page.get("total", 0) or 0

            markets.extend(page_results)
            skip += _PAGE_SIZE

            if not page_results or skip >= total:
                break

        # Build opportunities from active, liquid markets
        all_opps: list[MarketOpportunity] = []
        now = datetime.now(UTC)

        for market in markets:
            if not market.get("isActive", False):
                continue
            liq_usd = (market.get("liquidity") or {}).get("usd") or 0.0
            if liq_usd < _MIN_LIQUIDITY_USD:
                continue

            try:
                opps = self._build_market_opportunities(market, chain, now)
                for opp in opps:
                    if symbols and opp.asset_id not in symbols:
                        continue
                    all_opps.append(opp)
            except Exception as exc:
                log.warning(
                    "pendle_market_error",
                    market=market.get("address", ""),
                    chain=chain.value,
                    error=str(exc),
                )

        log.debug(
            "pendle_chain_done",
            chain=chain.value,
            total_fetched=len(markets),
            opportunities=len(all_opps),
        )
        return all_opps

    # -- Market → opportunities ------------------------------------------------

    def _build_market_opportunities(
        self,
        market: dict,
        chain: Chain,
        now: datetime,
    ) -> list[MarketOpportunity]:
        market_address = market["address"]
        expiry_str: str | None = market.get("expiry")

        if not expiry_str:
            return []

        maturity = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
        if maturity <= now:
            return []  # already matured

        days_to_maturity = (maturity - now).total_seconds() / 86400.0

        # Underlying asset
        underlying = market.get("underlyingAsset") or {}
        underlying_symbol = underlying.get("symbol", "")
        if not underlying_symbol:
            return []

        canonical = self.normalize_symbol(underlying_symbol, chain=chain)

        # APYs — decimal fractions → percent
        implied_apy_raw: float = market.get("impliedApy") or 0.0
        yt_floating_apy_raw: float = market.get("ytFloatingApy") or 0.0
        underlying_apy_raw: float = market.get("underlyingApy") or 0.0

        pt_apy_pct = implied_apy_raw * 100.0
        yt_apy_pct = yt_floating_apy_raw * 100.0
        underlying_apy_pct = underlying_apy_raw * 100.0

        liq_usd: float = (market.get("liquidity") or {}).get("usd") or 0.0

        # PT and YT token metadata
        pt_data = market.get("pt") or {}
        yt_data = market.get("yt") or {}
        pt_symbol = pt_data.get("symbol", f"PT-{underlying_symbol}")
        yt_symbol = yt_data.get("symbol", f"YT-{underlying_symbol}")

        # Maturity tag for deduplication and filtering
        maturity_tag = f"maturity-{maturity.strftime('%Y%m%d')}"
        source_url = f"https://app.pendle.finance/trade/pools/{market_address}"
        market_name_base = market.get("simpleName") or underlying_symbol

        results: list[MarketOpportunity] = []

        # ── PT opportunity ─────────────────────────────────────────────────
        pt_receipt = ReceiptTokenInfo(
            produces_receipt_token=True,
            receipt_token_symbol=pt_symbol,
            is_transferable=True,
            is_composable=True,
            notes=f"Pendle PT — redeems at par on {maturity.strftime('%d %b %Y')}",
        )

        results.append(self.build_opportunity(
            asset_id=canonical,
            asset_symbol=underlying_symbol,
            chain=chain.value,
            market_id=f"{market_address}:pt",
            market_name=f"Pendle PT {market_name_base} ({maturity.strftime('%d %b %Y')})",
            side=OpportunitySide.SUPPLY,
            opportunity_type=OpportunityType.PENDLE_PT,
            effective_duration=EffectiveDuration.FIXED_TERM,
            maturity_date=maturity,
            days_to_maturity=days_to_maturity,
            total_apy_pct=pt_apy_pct,
            base_apy_pct=pt_apy_pct,
            reward_breakdown=[
                RewardBreakdown(
                    reward_type=RewardType.NATIVE_YIELD,
                    apy_pct=pt_apy_pct,
                    is_variable=False,
                    notes=f"Implied fixed yield (underlying: {underlying_apy_pct:.2f}%)",
                )
            ],
            tvl_usd=liq_usd,
            total_supplied_usd=liq_usd,
            liquidity=LiquidityInfo(available_liquidity_usd=liq_usd),
            is_pendle=True,
            pendle_type="PT",
            receipt_token=pt_receipt,
            is_collateral_eligible=False,
            tags=["pendle", "fixed-yield", maturity_tag],
            source_url=source_url,
        ))

        # ── YT opportunity ─────────────────────────────────────────────────
        yt_receipt = ReceiptTokenInfo(
            produces_receipt_token=True,
            receipt_token_symbol=yt_symbol,
            is_transferable=True,
            is_composable=True,
            notes=(
                f"Pendle YT — receives all variable yield until "
                f"{maturity.strftime('%d %b %Y')}"
            ),
        )

        results.append(self.build_opportunity(
            asset_id=canonical,
            asset_symbol=underlying_symbol,
            chain=chain.value,
            market_id=f"{market_address}:yt",
            market_name=f"Pendle YT {market_name_base} ({maturity.strftime('%d %b %Y')})",
            side=OpportunitySide.SUPPLY,
            opportunity_type=OpportunityType.PENDLE_YT,
            effective_duration=EffectiveDuration.FIXED_TERM,
            maturity_date=maturity,
            days_to_maturity=days_to_maturity,
            total_apy_pct=yt_apy_pct,
            base_apy_pct=yt_apy_pct,
            reward_breakdown=[
                RewardBreakdown(
                    reward_type=RewardType.NATIVE_YIELD,
                    apy_pct=yt_apy_pct,
                    is_variable=True,
                    notes=(
                        f"Implied variable yield — leveraged {underlying_apy_pct:.2f}% "
                        f"underlying APY to maturity"
                    ),
                )
            ],
            tvl_usd=liq_usd,
            total_supplied_usd=liq_usd,
            liquidity=LiquidityInfo(available_liquidity_usd=liq_usd),
            is_pendle=True,
            pendle_type="YT",
            receipt_token=yt_receipt,
            is_collateral_eligible=False,
            tags=["pendle", "variable-yield", "leveraged", maturity_tag],
            source_url=source_url,
        ))

        return results

    # -- Health check ----------------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        try:
            page = await get_json(
                f"{settings.pendle_api_url}/v1/1/markets",
                params={"limit": 1, "isExpired": "false"},
            )
            ok = bool(page.get("results"))
            return {
                "status": "ok" if ok else "degraded",
                "last_success": self._last_success,
                "error": None,
            }
        except Exception as exc:
            return {"status": "down", "last_success": self._last_success, "error": str(exc)}
