"""
DeFiLlama catch-all adapter — broad protocol coverage for the long tail.

Serves as the fallback yield source for every DeFi protocol that does not have
a dedicated native adapter.  Consumes the public DeFiLlama yields API and
converts each pool to a :class:`MarketOpportunity`, skipping protocols already
covered by native adapters to avoid duplication.

Data sources (free, no API key required)
-----------------------------------------
  https://yields.llama.fi/pools               all yield pools (APY, TVL, chain)
  https://yields.llama.fi/chart/{pool_id}     30-day APY/TVL history per pool

Protocols covered (examples)
-----------------------------
  Fluid, Benqi, Venus, Radiant, Silo, Notional, Exactly, Clearpool, Goldfinch,
  Sturdy, Inverse Finance, Gearbox, dForce, Frax Lend, Cleo, Midas, Liqee,
  and hundreds more across 20+ chains.

Protocols skipped (native adapters exist — skip to avoid duplicates)
---------------------------------------------------------------------
  Aave V3/V2, Morpho (Blue + MetaMorpho), Compound V3/V2, Euler V2,
  SparkLend (+ Sky DSR/SSR), Kamino, Jupiter, Pendle.

APY encoding
------------
DeFiLlama /pools reports APYs already in percentage format (5.0 = 5%).
No ×100 multiplication is needed — unlike the Aave GraphQL API.

Limitations vs. native adapters
---------------------------------
  - No collateral matrix on BORROW opportunities (DeFiLlama doesn't expose it).
  - No supply/borrow caps (not in the /pools response).
  - LTV is reported as a raw decimal when present; many protocols omit it.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog

from app.connectors.base_adapter import ProtocolAdapter
from app.connectors.http_client import get_json, get_with_cache
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
# Endpoint URLs
# ---------------------------------------------------------------------------

_POOLS_URL = "https://yields.llama.fi/pools"
_CHART_BASE = "https://yields.llama.fi/chart"

# ---------------------------------------------------------------------------
# Filtering / history constants
# ---------------------------------------------------------------------------

_MIN_TVL_USD: float = 100_000.0
_TOP_HISTORY_COUNT: int = 100    # pull /chart history for this many top-TVL pools
_HISTORY_CONCURRENCY: int = 8    # max parallel /chart requests
_HISTORY_DAYS: int = 30          # days of history to attach

# ---------------------------------------------------------------------------
# Native-adapter skip set — DeFiLlama project slugs that have dedicated adapters
# ---------------------------------------------------------------------------

_SKIP_PROTOCOLS: frozenset[str] = frozenset({
    # AaveV3Adapter
    "aave-v3",
    "aave-v2",
    "aave",
    # MorphoAdapter
    "morpho-blue",
    "metamorpho",
    "morpho",
    # CompoundV3Adapter
    "compound-v3",
    "compound-v2",
    "compound",
    # EulerV2Adapter
    "euler-v2",
    "euler",
    # SparkAdapter (SparkLend + Sky DSR/SSR savings)
    "spark",
    "spark-lend",
    "sparklend",
    "maker-dsr",
    "sky",
    "sky-savings-rate",
    # KaminoAdapter
    "kamino",
    "kamino-lend",
    "kamino-liquidity",
    # JupiterAdapter
    "jupiter",
    "jupiter-lend",
    "jupiter-staked-sol",
    # PendleAdapter
    "pendle",
})

# ---------------------------------------------------------------------------
# DeFiLlama chain name → Chain enum
# ---------------------------------------------------------------------------

_CHAIN_MAP: dict[str, Chain] = {
    "Ethereum":   Chain.ETHEREUM,
    "Arbitrum":   Chain.ARBITRUM,
    "Optimism":   Chain.OPTIMISM,
    "Base":       Chain.BASE,
    "Polygon":    Chain.POLYGON,
    "Avalanche":  Chain.AVALANCHE,
    "BSC":        Chain.BSC,
    "Solana":     Chain.SOLANA,
    "Tron":       Chain.TRON,
    "Sui":        Chain.SUI,
    "Aptos":      Chain.APTOS,
    "Sei":        Chain.SEI,
    "Mantle":     Chain.MANTLE,
    "Scroll":     Chain.SCROLL,
    "Linea":      Chain.LINEA,
    "Blast":      Chain.BLAST,
    "Mode":       Chain.MODE,
    "Manta":      Chain.MANTA,
    "zkSync Era": Chain.ZKSYNC,
    "zkSync":     Chain.ZKSYNC,
    "Hyperliquid":Chain.HYPERLIQUID,
}

# De-duplicated ordered list for `supported_chains`
_SUPPORTED_CHAINS: list[Chain] = list(dict.fromkeys(_CHAIN_MAP.values()))

# ---------------------------------------------------------------------------
# DeFiLlama category → (OpportunityType, EffectiveDuration)
#
# Category comes from the /pools response "category" field (present on most
# pools; may be absent for obscure protocols, handled via _DEFAULT_CATEGORY).
# ---------------------------------------------------------------------------

_CATEGORY_MAP: dict[str, tuple[OpportunityType, EffectiveDuration]] = {
    "Lending":             (OpportunityType.LENDING,   EffectiveDuration.VARIABLE),
    "CDP":                 (OpportunityType.LENDING,   EffectiveDuration.VARIABLE),
    "Yield":               (OpportunityType.LENDING,   EffectiveDuration.VARIABLE),
    "Liquid Staking":      (OpportunityType.STAKING,   EffectiveDuration.OVERNIGHT),
    "Staking":             (OpportunityType.STAKING,   EffectiveDuration.OVERNIGHT),
    "Restaking":           (OpportunityType.RESTAKING, EffectiveDuration.VARIABLE),
    "Yield Aggregator":    (OpportunityType.VAULT,     EffectiveDuration.OVERNIGHT),
    "RWA":                 (OpportunityType.VAULT,     EffectiveDuration.OVERNIGHT),
    "RWA Lending":         (OpportunityType.VAULT,     EffectiveDuration.OVERNIGHT),
    "Leveraged Farming":   (OpportunityType.VAULT,     EffectiveDuration.OVERNIGHT),
    "Options Vault":       (OpportunityType.VAULT,     EffectiveDuration.OVERNIGHT),
    "Options":             (OpportunityType.VAULT,     EffectiveDuration.OVERNIGHT),
    "Structured Products": (OpportunityType.VAULT,     EffectiveDuration.OVERNIGHT),
    "Dexes":               (OpportunityType.AMM_LP,    EffectiveDuration.OVERNIGHT),
    "DEX":                 (OpportunityType.AMM_LP,    EffectiveDuration.OVERNIGHT),
}

# Default when category is absent or unrecognised
_DEFAULT_CATEGORY: tuple[OpportunityType, EffectiveDuration] = (
    OpportunityType.VAULT, EffectiveDuration.OVERNIGHT,
)

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _resolve_chain(chain_str: str) -> Chain | None:
    """Map a DeFiLlama chain name to our Chain enum; return None if unknown."""
    return _CHAIN_MAP.get(chain_str)


def _detect_lp(pool: dict, symbol: str) -> bool:
    """
    Return True when the pool is a multi-asset AMM LP position.

    Uses multiple DeFiLlama signals so we catch both CEX-style (Dexes) and
    DEX LP rows that may have incomplete category tagging:
      - ``exposure == "multi"``
      - ``ilRisk == "yes"``
      - Symbol contains "/" (e.g. "USDC/USDT")
      - Category is "Dexes" or "DEX"
    """
    if pool.get("exposure") == "multi":
        return True
    if (pool.get("ilRisk") or "").lower() == "yes":
        return True
    if "/" in symbol:
        return True
    cat = (pool.get("category") or "").lower()
    return cat in ("dexes", "dex")


def _detect_pendle(pool: dict) -> bool:
    """True when the pool is a Pendle PT/YT — already handled by PendleAdapter."""
    project = (pool.get("project") or "").lower()
    sym = (pool.get("symbol") or "").upper()
    return project == "pendle" or sym.startswith("PT-") or sym.startswith("YT-")


def _primary_symbol(symbol: str) -> str:
    """
    Extract the primary token from a potentially compound LP symbol.

      "USDC-USDT"     → "USDC"
      "stETH-ETH"     → "stETH"
      "USDC/USDT"     → "USDC"
      "WETH"          → "WETH"
      "ethereum:USDC" → "ethereum:USDC"  (normalizer strips chain prefix)
    """
    for sep in ("/", "-"):
        if sep in symbol:
            first = symbol.split(sep)[0].strip()
            # Guard: don't return empty string
            if first:
                return first
    return symbol


def _slug_to_name(slug: str) -> str:
    """Convert a DeFiLlama project slug to a display name.

    "fluid"           → "Fluid"
    "venus-protocol"  → "Venus Protocol"
    "radiant-capital" → "Radiant Capital"
    """
    return " ".join(word.capitalize() for word in slug.replace("-", " ").split())


def _format_history_point(point: dict) -> dict:
    """Normalise one /chart data point to a slim dict for storage."""
    return {
        "ts": point.get("timestamp", ""),
        "apy": _safe_float(point.get("apy")),
        "apy_base": _safe_float(point.get("apyBase")),
        "apy_reward": _safe_float(point.get("apyReward")),
        "tvl_usd": _safe_float(point.get("tvlUsd")),
    }


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class DeFiLlamaAdapter(ProtocolAdapter):
    """
    DeFiLlama catch-all adapter.

    Provides broad coverage of DeFi protocols without native adapters by
    ingesting the DeFiLlama yields /pools endpoint.  Each pool becomes a
    :class:`MarketOpportunity`; lending pools with borrow-APY data additionally
    emit a BORROW-side opportunity.

    Top pools (by TVL) also receive 30-day APY/TVL history fetched from the
    /chart/{pool_id} endpoint and stored in ``historical_rates_30d``.
    """

    # -- Abstract properties ---------------------------------------------------

    @property
    def venue(self) -> Venue:
        return Venue.DEFILLAMA

    @property
    def protocol_name(self) -> str:
        return "DeFiLlama"

    @property
    def protocol_slug(self) -> str:
        return "defillama"

    @property
    def supported_chains(self) -> list[Chain]:
        return _SUPPORTED_CHAINS

    @property
    def refresh_interval_seconds(self) -> int:
        return 600  # 10 minutes — DeFiLlama updates hourly at most

    @property
    def requires_api_key(self) -> bool:
        return False

    @property
    def api_key_env_var(self) -> str | None:
        return None

    # -- Main fetch ------------------------------------------------------------

    async def fetch_opportunities(
        self,
        symbols: list[str] | None = None,
        chains: list[Chain] | None = None,
    ) -> list[MarketOpportunity]:
        """Fetch DeFiLlama pools and convert to MarketOpportunity instances.

        Steps:
          1. Fetch all pools from /pools (Redis-cached for the refresh interval).
          2. For each pool: skip if in native-adapter list, unknown chain, or
             below the TVL floor, then build supply (+ optional borrow) opportunity.
          3. For the top ``_TOP_HISTORY_COUNT`` pools by TVL, concurrently fetch
             30-day history from /chart/{pool_id} and attach to supply opportunities.
        """
        # 1. Fetch pools (cached)
        raw_data = await get_with_cache(
            _POOLS_URL,
            cache_key="defillama:pools",
            ttl_seconds=self.refresh_interval_seconds,
        )
        raw_pools: list[dict] = raw_data.get("data", []) if isinstance(raw_data, dict) else []

        if not raw_pools:
            log.warning("defillama_no_pools_returned")
            return []

        log.debug("defillama_pools_raw", count=len(raw_pools))

        # 2. Chain filter set
        accepted_chains: set[Chain] | None = set(chains) if chains else None

        # 3. Parse pools → opportunities
        all_opps: list[MarketOpportunity] = []
        history_candidates: list[tuple[float, str]] = []  # (tvl, pool_id)

        for raw in raw_pools:
            try:
                opps, pool_id, tvl = self._parse_pool(raw, accepted_chains, symbols)
                all_opps.extend(opps)
                if opps and pool_id and tvl is not None:
                    history_candidates.append((tvl, pool_id))
            except Exception as exc:
                pid = raw.get("pool", "?")
                log.debug(
                    "defillama_pool_parse_error",
                    pool=pid,
                    project=raw.get("project", "?"),
                    error=str(exc),
                )

        # 4. Fetch history for top pools concurrently
        history_candidates.sort(key=lambda x: x[0], reverse=True)
        top_pool_ids = [pid for _, pid in history_candidates[:_TOP_HISTORY_COUNT]]

        if top_pool_ids:
            history_map = await self._fetch_histories(top_pool_ids)
            all_opps = _attach_history(all_opps, history_map)

        log.info(
            "defillama_fetch_done",
            total=len(all_opps),
            pools_scanned=len(raw_pools),
            history_pools=len(top_pool_ids),
        )
        return all_opps

    # -- Pool parser -----------------------------------------------------------

    def _parse_pool(
        self,
        raw: dict,
        accepted_chains: set[Chain] | None,
        symbols: list[str] | None,
    ) -> tuple[list[MarketOpportunity], str, float | None]:
        """Parse one raw DeFiLlama pool dict into 0–2 MarketOpportunity objects.

        Returns ``(opportunities, pool_id, tvl_usd)``.
        ``pool_id`` and ``tvl_usd`` are always returned even when opportunities
        is empty so the caller can log / track skips.
        """
        project = (raw.get("project") or "").strip().lower()
        pool_id: str = raw.get("pool") or ""
        chain_str: str = raw.get("chain") or ""
        symbol_raw: str = raw.get("symbol") or ""
        category: str = raw.get("category") or ""
        tvl_usd = _safe_float(raw.get("tvlUsd"))

        # ── Skip checks ────────────────────────────────────────────────────
        if project in _SKIP_PROTOCOLS:
            return [], pool_id, tvl_usd
        if _detect_pendle(raw):
            return [], pool_id, tvl_usd
        if tvl_usd is None or tvl_usd < _MIN_TVL_USD:
            return [], pool_id, tvl_usd

        # ── Chain resolution ───────────────────────────────────────────────
        chain = _resolve_chain(chain_str)
        if chain is None:
            return [], pool_id, tvl_usd
        if accepted_chains is not None and chain not in accepted_chains:
            return [], pool_id, tvl_usd

        # ── LP / Pendle detection ──────────────────────────────────────────
        is_lp_pool = _detect_lp(raw, symbol_raw) or self.detect_and_skip_amm_lp(symbol_raw)

        # ── Symbol normalisation ───────────────────────────────────────────
        primary_sym = _primary_symbol(symbol_raw)
        canonical = self.normalize_symbol(primary_sym, chain=chain)
        asset_symbol = primary_sym

        # Apply symbol filter after normalisation
        if symbols and canonical not in symbols:
            return [], pool_id, tvl_usd

        # ── APYs — already in % from DeFiLlama (no ×100 needed) ──────────
        apy_total = _safe_float(raw.get("apy")) or 0.0
        apy_base = _safe_float(raw.get("apyBase")) or 0.0
        apy_reward = _safe_float(raw.get("apyReward")) or 0.0
        apy_base_borrow = _safe_float(raw.get("apyBaseBorrow"))
        apy_reward_borrow = _safe_float(raw.get("apyRewardBorrow")) or 0.0

        # ── Size & capacity ────────────────────────────────────────────────
        total_supply_usd = _safe_float(raw.get("totalSupplyUsd"))
        total_borrow_usd = _safe_float(raw.get("totalBorrowUsd"))
        ltv_raw = _safe_float(raw.get("ltv"))  # 0.0–1.0 decimal fraction when present

        # ── Classify opportunity type ──────────────────────────────────────
        if is_lp_pool:
            opp_type = OpportunityType.AMM_LP
            duration = EffectiveDuration.OVERNIGHT
        else:
            # Prefer category → type; fall back to inferring from borrow data
            if category in _CATEGORY_MAP:
                opp_type, duration = _CATEGORY_MAP[category]
            elif apy_base_borrow is not None:
                # Has borrow APY → treat as lending even without category
                opp_type, duration = OpportunityType.LENDING, EffectiveDuration.VARIABLE
            else:
                opp_type, duration = _DEFAULT_CATEGORY

        # ── Liquidity ─────────────────────────────────────────────────────
        avail_usd: float | None = None
        util_pct: float | None = None
        if total_supply_usd is not None and total_borrow_usd is not None:
            avail_usd = max(total_supply_usd - total_borrow_usd, 0.0)
            if total_supply_usd > 0:
                util_pct = min(total_borrow_usd / total_supply_usd * 100.0, 100.0)

        liquidity = LiquidityInfo(
            available_liquidity_usd=avail_usd,
            utilization_rate_pct=util_pct,
        )

        rate_model = RateModelInfo(
            model_type=f"defillama-{project}",
            current_supply_rate_pct=apy_base,
            current_borrow_rate_pct=apy_base_borrow,
        )

        # ── Display metadata ───────────────────────────────────────────────
        protocol_display = _slug_to_name(project)
        market_name = f"{protocol_display} {symbol_raw}"
        source_url = f"https://defillama.com/protocol/{project}"

        # ── Tags ───────────────────────────────────────────────────────────
        tags: list[str] = []
        if is_lp_pool:
            tags.append("amm-lp")
        if raw.get("stablecoin"):
            tags.append("stablecoin")
        il_risk = raw.get("ilRisk")
        if il_risk and il_risk.lower() != "no":
            tags.append(f"il:{il_risk}")

        # ── Reward breakdowns ──────────────────────────────────────────────
        reward_tokens: list[str] = raw.get("rewardTokens") or []

        supply_rewards = _build_supply_rewards(apy_base, apy_reward, reward_tokens)

        results: list[MarketOpportunity] = []

        # ── SUPPLY opportunity ─────────────────────────────────────────────
        results.append(self.build_opportunity(
            asset_id=canonical,
            asset_symbol=asset_symbol,
            chain=chain.value,
            market_id=pool_id,
            market_name=market_name,
            side=OpportunitySide.SUPPLY,
            opportunity_type=opp_type,
            effective_duration=duration,
            total_apy_pct=apy_total,
            base_apy_pct=apy_base,
            reward_breakdown=supply_rewards,
            total_supplied_usd=total_supply_usd if total_supply_usd is not None else tvl_usd,
            tvl_usd=tvl_usd,
            liquidity=liquidity,
            rate_model=rate_model,
            is_amm_lp=is_lp_pool,
            is_collateral_eligible=bool(ltv_raw and ltv_raw > 0),
            as_collateral_max_ltv_pct=(ltv_raw * 100.0 if ltv_raw else None),
            is_capacity_capped=False,  # DeFiLlama does not expose caps
            tags=tags,
            source_url=source_url,
            # Override adapter-level protocol metadata with per-pool project info
            protocol=protocol_display,
            protocol_slug=project,
            data_source="defillama",
        ))

        # ── BORROW opportunity ─────────────────────────────────────────────
        # Only create when DeFiLlama explicitly reports borrow APY data and
        # the pool is not a multi-asset LP (which has no borrow side).
        if not is_lp_pool and apy_base_borrow is not None:
            borrow_net_apy = max(apy_base_borrow - apy_reward_borrow, 0.0)
            borrow_rewards = _build_borrow_rewards(
                apy_base_borrow, apy_reward_borrow, reward_tokens,
            )
            results.append(self.build_opportunity(
                asset_id=canonical,
                asset_symbol=asset_symbol,
                chain=chain.value,
                market_id=f"{pool_id}:borrow",
                market_name=market_name,
                side=OpportunitySide.BORROW,
                opportunity_type=OpportunityType.LENDING,
                effective_duration=EffectiveDuration.VARIABLE,
                total_apy_pct=borrow_net_apy,
                base_apy_pct=apy_base_borrow,
                reward_breakdown=borrow_rewards,
                total_borrowed_usd=total_borrow_usd,
                liquidity=liquidity,
                rate_model=rate_model,
                # Collateral matrix unavailable from DeFiLlama /pools
                collateral_options=None,
                tags=tags,
                source_url=source_url,
                protocol=protocol_display,
                protocol_slug=project,
                data_source="defillama",
            ))

        return results, pool_id, tvl_usd

    # -- Historical data -------------------------------------------------------

    async def _fetch_histories(
        self,
        pool_ids: list[str],
    ) -> dict[str, list[dict]]:
        """Fetch 30-day chart history for pool IDs, bounded by ``_HISTORY_CONCURRENCY``."""
        semaphore = asyncio.Semaphore(_HISTORY_CONCURRENCY)

        async def _one(pool_id: str) -> tuple[str, list[dict]]:
            async with semaphore:
                try:
                    data = await get_json(f"{_CHART_BASE}/{pool_id}")
                    points: list[dict] = (
                        data.get("data", []) if isinstance(data, dict) else []
                    )
                    recent = points[-_HISTORY_DAYS:] if len(points) > _HISTORY_DAYS else points
                    return pool_id, [_format_history_point(p) for p in recent]
                except Exception as exc:
                    log.debug("defillama_chart_error", pool=pool_id, error=str(exc))
                    return pool_id, []

        results = await asyncio.gather(*[_one(pid) for pid in pool_ids])
        return {pid: pts for pid, pts in results if pts}

    # -- Health check ----------------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        """Lightweight probe: verify /pools returns at least one row."""
        try:
            data = await get_json(_POOLS_URL)
            pools = data.get("data", []) if isinstance(data, dict) else []
            ok = len(pools) > 0
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


# ---------------------------------------------------------------------------
# Module-level pure helpers (no adapter state needed)
# ---------------------------------------------------------------------------


def _build_supply_rewards(
    apy_base: float,
    apy_reward: float,
    reward_tokens: list[str],
) -> list[RewardBreakdown]:
    rewards: list[RewardBreakdown] = [
        RewardBreakdown(
            reward_type=RewardType.NATIVE_YIELD,
            apy_pct=apy_base,
            is_variable=True,
            notes="Base supply APY",
        ),
    ]
    if apy_reward > 0:
        rewards.append(
            RewardBreakdown(
                reward_type=RewardType.TOKEN_INCENTIVE,
                token_id=(reward_tokens[0] if reward_tokens else None),
                apy_pct=apy_reward,
                is_variable=True,
                notes="Reward token incentive",
            )
        )
    return rewards


def _build_borrow_rewards(
    apy_base_borrow: float,
    apy_reward_borrow: float,
    reward_tokens: list[str],
) -> list[RewardBreakdown]:
    rewards: list[RewardBreakdown] = [
        RewardBreakdown(
            reward_type=RewardType.NATIVE_YIELD,
            apy_pct=apy_base_borrow,
            is_variable=True,
            notes="Base borrow APY (cost)",
        ),
    ]
    if apy_reward_borrow > 0:
        rewards.append(
            RewardBreakdown(
                reward_type=RewardType.TOKEN_INCENTIVE,
                token_id=(reward_tokens[0] if reward_tokens else None),
                apy_pct=apy_reward_borrow,
                is_variable=True,
                notes="Reward token reduces net borrow cost",
            )
        )
    return rewards


def _attach_history(
    opps: list[MarketOpportunity],
    history_map: dict[str, list[dict]],
) -> list[MarketOpportunity]:
    """
    Return a new list with ``historical_rates_30d`` attached to SUPPLY-side
    opportunities whose pool_id appears in *history_map*.

    Only the SUPPLY side carries history (avoids storing it twice when both a
    supply and a borrow opportunity exist for the same DeFiLlama pool).
    """
    updated: list[MarketOpportunity] = []
    for opp in opps:
        if opp.side == OpportunitySide.SUPPLY:
            # market_id is pool_id for supply, pool_id:borrow for borrow
            hist = history_map.get(opp.market_id)
            if hist:
                opp = opp.model_copy(update={"historical_rates_30d": hist})
        updated.append(opp)
    return updated
