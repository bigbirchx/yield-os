"""
Venue-specific symbol normalisation for Yield OS.

Every exchange, lending protocol, and data aggregator uses slightly different
naming for the same asset.  This module translates venue-native symbols into
the canonical IDs defined in :mod:`taxonomy`.

Usage::

    from asset_registry.normalization import AssetNormalizer, Venue

    n = AssetNormalizer()
    n.normalize(Venue.AAVE_V3, "USDC.e", chain=Chain.ARBITRUM)   # -> "USDC"
    n.normalize(Venue.COINGECKO, "ethereum")                       # -> "ETH"
    n.is_amm_lp(Venue.DEFILLAMA, "UNI-V2 WETH/USDC")             # -> True
    n.is_pendle(Venue.DEFILLAMA, "PT-stETH-26DEC2025")            # -> (True, "PT")
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Callable

from pydantic import BaseModel

from .taxonomy import ASSET_REGISTRY, AssetDefinition, Chain

# Type for the optional fallback lookup function
_FallbackLookup = Callable[[str], str | None]

# Module-level fallback shared by all AssetNormalizer instances.
# Set via ``set_global_fallback_lookup()`` at startup.
_global_fallback: _FallbackLookup | None = None


def set_global_fallback_lookup(fn: _FallbackLookup | None) -> None:
    """Register a global fallback callable for all normalizer instances.

    Called at startup by :class:`TokenUniverseService` to resolve symbols
    not in the static registry.
    """
    global _global_fallback
    _global_fallback = fn


# ---------------------------------------------------------------------------
# Venue enum
# ---------------------------------------------------------------------------


class Venue(str, Enum):
    """Every data source Yield OS integrates with."""

    # Exchanges
    BINANCE = "BINANCE"
    OKX = "OKX"
    BYBIT = "BYBIT"
    DERIBIT = "DERIBIT"
    CME = "CME"
    BULLISH = "BULLISH"
    COINBASE = "COINBASE"
    HYPERLIQUID = "HYPERLIQUID"

    # DeFi Lending
    AAVE_V3 = "AAVE_V3"
    MORPHO = "MORPHO"
    COMPOUND_V3 = "COMPOUND_V3"
    EULER_V2 = "EULER_V2"
    SPARK = "SPARK"
    KAMINO = "KAMINO"
    JUPITER = "JUPITER"
    JUSTLEND = "JUSTLEND"
    KATANA = "KATANA"
    SKY = "SKY"

    # DeFi Other
    ETHERFI = "ETHERFI"
    PENDLE = "PENDLE"
    LIDO = "LIDO"
    ROCKETPOOL = "ROCKETPOOL"
    EIGENLAYER = "EIGENLAYER"

    # Future / stub adapters
    VENUS = "VENUS"
    RADIANT = "RADIANT"
    FLUID = "FLUID"
    BENQI = "BENQI"
    SILO = "SILO"

    # Aggregators
    DEFILLAMA = "DEFILLAMA"
    COINGECKO = "COINGECKO"
    COINGLASS = "COINGLASS"


# ---------------------------------------------------------------------------
# Mapping model
# ---------------------------------------------------------------------------


class VenueAssetMapping(BaseModel):
    """One mapping from a venue-specific symbol to our canonical ID."""

    venue: Venue
    venue_symbol: str
    canonical_id: str
    chain: Chain | None = None
    is_contract_address: bool = False
    notes: str | None = None

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Static mapping table
# ---------------------------------------------------------------------------


def _m(
    venue: Venue,
    venue_symbol: str,
    canonical_id: str,
    chain: Chain | None = None,
    is_contract_address: bool = False,
    notes: str | None = None,
) -> VenueAssetMapping:
    return VenueAssetMapping(
        venue=venue,
        venue_symbol=venue_symbol,
        canonical_id=canonical_id,
        chain=chain,
        is_contract_address=is_contract_address,
        notes=notes,
    )


_V = Venue
_C = Chain

VENUE_MAPPINGS: list[VenueAssetMapping] = [
    # ── BINANCE ───────────────────────────────────────────────────────────
    _m(_V.BINANCE, "BTC", "BTC"),
    _m(_V.BINANCE, "ETH", "ETH"),
    _m(_V.BINANCE, "SOL", "SOL"),
    _m(_V.BINANCE, "USDT", "USDT"),
    _m(_V.BINANCE, "USDC", "USDC"),
    _m(_V.BINANCE, "FDUSD", "FDUSD"),
    _m(_V.BINANCE, "TUSD", "TUSD"),
    _m(_V.BINANCE, "WBTC", "WBTC"),
    _m(_V.BINANCE, "WBETH", "WBETH", notes="Binance Wrapped Beacon ETH (liquid staking)"),

    # ── OKX ───────────────────────────────────────────────────────────────
    _m(_V.OKX, "BTC", "BTC"),
    _m(_V.OKX, "ETH", "ETH"),
    _m(_V.OKX, "SOL", "SOL"),
    _m(_V.OKX, "USDT", "USDT"),
    _m(_V.OKX, "USDC", "USDC"),

    # ── BYBIT ─────────────────────────────────────────────────────────────
    _m(_V.BYBIT, "BTC", "BTC"),
    _m(_V.BYBIT, "ETH", "ETH"),
    _m(_V.BYBIT, "SOL", "SOL"),
    _m(_V.BYBIT, "USDT", "USDT"),
    _m(_V.BYBIT, "USDC", "USDC"),

    # ── DERIBIT ───────────────────────────────────────────────────────────
    _m(_V.DERIBIT, "BTC", "BTC"),
    _m(_V.DERIBIT, "ETH", "ETH"),
    _m(_V.DERIBIT, "SOL", "SOL"),
    _m(_V.DERIBIT, "USDC", "USDC"),

    # ── AAVE_V3 ───────────────────────────────────────────────────────────
    # WETH per chain
    _m(_V.AAVE_V3, "WETH", "WETH", chain=_C.ETHEREUM),
    _m(_V.AAVE_V3, "WETH", "WETH", chain=_C.ARBITRUM),
    _m(_V.AAVE_V3, "WETH", "WETH", chain=_C.OPTIMISM),
    _m(_V.AAVE_V3, "WETH", "WETH", chain=_C.BASE),
    # USDC variants
    _m(_V.AAVE_V3, "USDC", "USDC"),
    _m(_V.AAVE_V3, "USDC.e", "USDC", chain=_C.ARBITRUM, notes="Bridged USDC on Arbitrum"),
    _m(_V.AAVE_V3, "USDC.e", "USDC", chain=_C.AVALANCHE, notes="Bridged USDC on Avalanche"),
    _m(_V.AAVE_V3, "USDbC", "USDC", chain=_C.BASE, notes="Bridged USDC on Base"),
    # Other stables
    _m(_V.AAVE_V3, "USDT", "USDT"),
    _m(_V.AAVE_V3, "DAI", "DAI"),
    _m(_V.AAVE_V3, "USDS", "USDS"),
    # BTC family
    _m(_V.AAVE_V3, "WBTC", "WBTC"),
    _m(_V.AAVE_V3, "cbBTC", "cbBTC"),
    _m(_V.AAVE_V3, "tBTC", "tBTC"),
    # ETH LSTs / LRTs
    _m(_V.AAVE_V3, "wstETH", "wstETH"),
    _m(_V.AAVE_V3, "cbETH", "cbETH"),
    _m(_V.AAVE_V3, "rETH", "rETH"),
    _m(_V.AAVE_V3, "weETH", "weETH"),
    _m(_V.AAVE_V3, "rsETH", "rsETH"),
    _m(_V.AAVE_V3, "ezETH", "ezETH"),
    # Aave-native + Ethena
    _m(_V.AAVE_V3, "GHO", "GHO"),
    _m(_V.AAVE_V3, "USDe", "USDe"),
    _m(_V.AAVE_V3, "sUSDe", "sUSDe"),

    # ── MORPHO ────────────────────────────────────────────────────────────
    _m(_V.MORPHO, "WETH", "WETH"),
    _m(_V.MORPHO, "Wrapped Ether", "WETH"),
    _m(_V.MORPHO, "USDC", "USDC"),
    _m(_V.MORPHO, "USD Coin", "USDC"),
    _m(_V.MORPHO, "USDT", "USDT"),
    _m(_V.MORPHO, "Tether USD", "USDT"),
    _m(_V.MORPHO, "wstETH", "wstETH"),
    _m(_V.MORPHO, "Wrapped liquid staked Ether 2.0", "wstETH"),
    _m(_V.MORPHO, "LBTC", "LBTC"),
    _m(_V.MORPHO, "cbBTC", "cbBTC"),

    # ── COMPOUND_V3 ───────────────────────────────────────────────────────
    _m(_V.COMPOUND_V3, "WETH", "WETH"),
    _m(_V.COMPOUND_V3, "USDC", "USDC"),
    _m(_V.COMPOUND_V3, "USDT", "USDT"),
    _m(_V.COMPOUND_V3, "WBTC", "WBTC"),
    _m(_V.COMPOUND_V3, "cbBTC", "cbBTC"),
    _m(_V.COMPOUND_V3, "wstETH", "wstETH"),

    # ── DEFILLAMA ─────────────────────────────────────────────────────────
    _m(_V.DEFILLAMA, "WETH", "WETH"),
    _m(_V.DEFILLAMA, "USDC", "USDC"),
    _m(_V.DEFILLAMA, "USDT", "USDT"),
    _m(_V.DEFILLAMA, "DAI", "DAI"),
    _m(_V.DEFILLAMA, "stETH", "stETH"),
    _m(_V.DEFILLAMA, "wstETH", "wstETH"),
    _m(_V.DEFILLAMA, "WBTC", "WBTC"),
    _m(_V.DEFILLAMA, "ETH", "ETH"),
    _m(_V.DEFILLAMA, "BTC", "BTC"),
    _m(_V.DEFILLAMA, "SOL", "SOL"),
    _m(_V.DEFILLAMA, "cbETH", "cbETH"),
    _m(_V.DEFILLAMA, "rETH", "rETH"),
    _m(_V.DEFILLAMA, "weETH", "weETH"),
    _m(_V.DEFILLAMA, "cbBTC", "cbBTC"),
    _m(_V.DEFILLAMA, "mSOL", "mSOL"),
    _m(_V.DEFILLAMA, "jitoSOL", "jitoSOL"),
    _m(_V.DEFILLAMA, "bSOL", "bSOL"),
    _m(_V.DEFILLAMA, "sDAI", "sDAI"),
    _m(_V.DEFILLAMA, "sUSDe", "sUSDe"),
    _m(_V.DEFILLAMA, "USDe", "USDe"),
    _m(_V.DEFILLAMA, "GHO", "GHO"),
    _m(_V.DEFILLAMA, "FRAX", "FRAX"),
    _m(_V.DEFILLAMA, "crvUSD", "crvUSD"),
    _m(_V.DEFILLAMA, "PYUSD", "PYUSD"),

    # ── KAMINO ────────────────────────────────────────────────────────────
    _m(_V.KAMINO, "SOL", "SOL"),
    _m(_V.KAMINO, "WSOL", "SOL", notes="Wrapped SOL → canonical SOL"),
    _m(_V.KAMINO, "mSOL", "mSOL"),
    _m(_V.KAMINO, "jitoSOL", "jitoSOL"),
    _m(_V.KAMINO, "JITOSOL", "jitoSOL"),
    _m(_V.KAMINO, "bSOL", "bSOL"),
    _m(_V.KAMINO, "JupSOL", "JupSOL"),
    _m(_V.KAMINO, "JUPSOL", "JupSOL"),
    _m(_V.KAMINO, "USDC", "USDC"),
    _m(_V.KAMINO, "USDT", "USDT"),
    _m(_V.KAMINO, "USDS", "USDS"),
    _m(_V.KAMINO, "PYUSD", "PYUSD"),
    _m(_V.KAMINO, "ETH", "ETH"),
    _m(_V.KAMINO, "WBTC", "WBTC"),
    _m(_V.KAMINO, "cbBTC", "cbBTC"),
    _m(_V.KAMINO, "CBBTC", "cbBTC"),
    _m(_V.KAMINO, "tBTC", "tBTC"),
    _m(_V.KAMINO, "LBTC", "LBTC"),
    _m(_V.KAMINO, "wstETH", "wstETH"),
    _m(_V.KAMINO, "JLP", "JLP"),

    # ── PENDLE ────────────────────────────────────────────────────────────
    # Pendle underlyingAsset.symbol values that need explicit mapping.
    # Most canonical IDs already match directly via identity lookup; only
    # exceptions (case mismatches, aliases) need entries here.
    _m(_V.PENDLE, "stETH", "stETH"),
    _m(_V.PENDLE, "wstETH", "wstETH"),
    _m(_V.PENDLE, "eETH", "eETH"),
    _m(_V.PENDLE, "weETH", "weETH"),
    _m(_V.PENDLE, "rETH", "rETH"),
    _m(_V.PENDLE, "cbETH", "cbETH"),
    _m(_V.PENDLE, "rsETH", "rsETH"),
    _m(_V.PENDLE, "ezETH", "ezETH"),
    _m(_V.PENDLE, "USDe", "USDe"),
    _m(_V.PENDLE, "sUSDe", "sUSDe"),
    _m(_V.PENDLE, "USDC", "USDC"),
    _m(_V.PENDLE, "USDT", "USDT"),
    _m(_V.PENDLE, "DAI", "DAI"),
    _m(_V.PENDLE, "USDS", "USDS"),
    _m(_V.PENDLE, "GHO", "GHO"),
    _m(_V.PENDLE, "WETH", "WETH"),
    _m(_V.PENDLE, "WBTC", "WBTC"),
    _m(_V.PENDLE, "cbBTC", "cbBTC"),
    _m(_V.PENDLE, "sDAI", "sDAI"),

    # ── JUPITER ───────────────────────────────────────────────────────────
    _m(_V.JUPITER, "SOL", "SOL"),
    _m(_V.JUPITER, "WSOL", "SOL", notes="Wrapped SOL → canonical SOL"),
    _m(_V.JUPITER, "USDC", "USDC"),
    _m(_V.JUPITER, "USDT", "USDT"),
    _m(_V.JUPITER, "USDS", "USDS"),
    _m(_V.JUPITER, "PYUSD", "PYUSD"),
    _m(_V.JUPITER, "LBTC", "LBTC"),
    _m(_V.JUPITER, "cbBTC", "cbBTC"),
    _m(_V.JUPITER, "CBBTC", "cbBTC"),
    _m(_V.JUPITER, "JLP", "JLP"),
    _m(_V.JUPITER, "JUPSOL", "JupSOL"),
    _m(_V.JUPITER, "JupSOL", "JupSOL"),

    # ── JUSTLEND ──────────────────────────────────────────────────────────
    # JustLend is the dominant Tron lending protocol.
    # TRX, JST, SUN, BTT are Tron-native and use identity passthrough.
    _m(_V.JUSTLEND, "USDT", "USDT", chain=_C.TRON, notes="TRC-20 USDT — dominant Tron stablecoin"),
    _m(_V.JUSTLEND, "USDC", "USDC", chain=_C.TRON),
    _m(_V.JUSTLEND, "WBTC", "WBTC", chain=_C.TRON, notes="TRC-20 wrapped BTC"),
    _m(_V.JUSTLEND, "WETH", "WETH", chain=_C.TRON, notes="TRC-20 wrapped ETH"),
    _m(_V.JUSTLEND, "TUSD", "TUSD"),

    # ── KATANA ────────────────────────────────────────────────────────────
    # Katana — yield-strategy vaults, data sourced from DeFiLlama.
    # Chain assignments come from the DeFiLlama response; explicit mappings
    # cover the handful of symbols that differ from canonical IDs.
    _m(_V.KATANA, "WETH", "WETH"),
    _m(_V.KATANA, "WBTC", "WBTC"),
    _m(_V.KATANA, "USDC", "USDC"),
    _m(_V.KATANA, "USDT", "USDT"),
    _m(_V.KATANA, "wstETH", "wstETH"),
    _m(_V.KATANA, "cbETH", "cbETH"),

    # ── COINGLASS ─────────────────────────────────────────────────────────
    _m(_V.COINGLASS, "BTC", "BTC"),
    _m(_V.COINGLASS, "ETH", "ETH"),
    _m(_V.COINGLASS, "SOL", "SOL"),

    # COINGECKO mappings are built dynamically from the taxonomy
    # (see _build_coingecko_reverse_map below)
]


# ---------------------------------------------------------------------------
# Build CoinGecko reverse lookup from taxonomy.ASSET_REGISTRY
# ---------------------------------------------------------------------------

def _build_coingecko_mappings() -> list[VenueAssetMapping]:
    """Generate COINGECKO venue mappings from every asset that has a coingecko_id."""
    out: list[VenueAssetMapping] = []
    for asset in ASSET_REGISTRY.values():
        if asset.coingecko_id:
            out.append(_m(_V.COINGECKO, asset.coingecko_id, asset.canonical_id))
    return out


# Append the auto-generated CoinGecko mappings once at import time.
VENUE_MAPPINGS.extend(_build_coingecko_mappings())


# ---------------------------------------------------------------------------
# Heuristic patterns
# ---------------------------------------------------------------------------

# AMM LP token detection patterns (case-insensitive)
_LP_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bLP\b", re.IGNORECASE),
    re.compile(r"\bAMM\b", re.IGNORECASE),
    re.compile(r"\bGAMM\b", re.IGNORECASE),
    re.compile(r"\bSLP\b", re.IGNORECASE),
    re.compile(r"\bUNI-V[23]\b", re.IGNORECASE),
    re.compile(r"\bCAKE-LP\b", re.IGNORECASE),
    re.compile(r"\bSUSHI-LP\b", re.IGNORECASE),
    re.compile(r"\bVELO-LP\b", re.IGNORECASE),
    re.compile(r"\bAERO-LP\b", re.IGNORECASE),
    re.compile(r"\bCRV\b.*\bLP\b", re.IGNORECASE),
    re.compile(r"\bBAL\b.*\bLP\b", re.IGNORECASE),
    re.compile(r"^[A-Z0-9]+-[A-Z0-9]+\s+LP$", re.IGNORECASE),
]

# Pendle PT / YT detection
_PENDLE_PT_RE = re.compile(
    r"^PT[- ](.+?)(?:[- ](\d{1,2}[A-Z]{3}\d{2,4}))?\s*$",
    re.IGNORECASE,
)
_PENDLE_YT_RE = re.compile(
    r"^YT[- ](.+?)(?:[- ](\d{1,2}[A-Z]{3}\d{2,4}))?\s*$",
    re.IGNORECASE,
)

# DeFiLlama chain-prefixed symbol pattern: "ethereum:USDC" -> "USDC"
_CHAIN_PREFIX_RE = re.compile(r"^[a-zA-Z0-9_-]+:(.+)$")


# ---------------------------------------------------------------------------
# Normalizer
# ---------------------------------------------------------------------------


class AssetNormalizer:
    """Stateful normalizer that resolves venue-specific symbols to canonical IDs.

    Pre-loaded with :data:`VENUE_MAPPINGS` at construction time.  Additional
    mappings can be registered at runtime via :meth:`register_mapping`.

    An optional *fallback lookup* callable can be set via
    :meth:`set_fallback_lookup`.  When the static mapping tables and identity
    check fail, the normalizer calls this function as a last resort before
    returning ``None``.  This is used by :class:`TokenUniverseService` to
    resolve top-500 tokens that aren't in the static registry.
    """

    def __init__(self) -> None:
        # Primary index: (venue, venue_symbol_lower, chain | None) -> canonical_id
        self._forward: dict[tuple[Venue, str, Chain | None], str] = {}
        # Secondary index (chain-agnostic): (venue, venue_symbol_lower) -> canonical_id
        self._forward_any_chain: dict[tuple[Venue, str], str] = {}
        # Reverse index: canonical_id -> list[VenueAssetMapping]
        self._reverse: dict[str, list[VenueAssetMapping]] = {}
        # All mappings for iteration / introspection
        self._all: list[VenueAssetMapping] = []
        # Optional fallback for dynamic token resolution
        self._fallback_lookup: _FallbackLookup | None = None

        for mapping in VENUE_MAPPINGS:
            self._index(mapping)

    def set_fallback_lookup(self, fn: _FallbackLookup) -> None:
        """Register a fallback callable ``fn(raw_symbol) -> canonical_id | None``.

        Called when all static mapping and identity lookups fail.
        """
        self._fallback_lookup = fn

    # -- internal ------------------------------------------------------------

    def _index(self, mapping: VenueAssetMapping) -> None:
        key_lower = mapping.venue_symbol.lower()

        # Chain-specific lookup
        self._forward[(mapping.venue, key_lower, mapping.chain)] = mapping.canonical_id

        # Chain-agnostic lookup (first-registered wins)
        any_key = (mapping.venue, key_lower)
        if any_key not in self._forward_any_chain:
            self._forward_any_chain[any_key] = mapping.canonical_id

        # Reverse
        self._reverse.setdefault(mapping.canonical_id, []).append(mapping)

        self._all.append(mapping)

    @staticmethod
    def _strip_chain_prefix(raw: str) -> str:
        """Remove DeFiLlama-style ``chain:SYMBOL`` prefixes."""
        m = _CHAIN_PREFIX_RE.match(raw)
        return m.group(1) if m else raw

    # -- public API ----------------------------------------------------------

    def normalize(
        self,
        venue: Venue,
        raw_symbol: str,
        chain: Chain | None = None,
    ) -> str | None:
        """Resolve a venue-specific symbol to the canonical ID.

        Returns ``None`` when no mapping is found.

        Resolution order:
        1. Exact match with venue + symbol + chain
        2. Exact match with venue + symbol (chain-agnostic)
        3. After stripping DeFiLlama-style ``chain:`` prefix, repeat 1–2
        4. Direct match against ``ASSET_REGISTRY`` keys (identity mapping)
        """
        sym = raw_symbol.strip()
        sym_lower = sym.lower()

        # 1) Exact chain-specific
        hit = self._forward.get((venue, sym_lower, chain))
        if hit is not None:
            return hit

        # 2) Chain-agnostic
        hit = self._forward_any_chain.get((venue, sym_lower))
        if hit is not None:
            return hit

        # 3) Strip chain prefix (e.g. "ethereum:USDC") and retry
        stripped = self._strip_chain_prefix(sym)
        if stripped != sym:
            stripped_lower = stripped.lower()
            hit = self._forward.get((venue, stripped_lower, chain))
            if hit is not None:
                return hit
            hit = self._forward_any_chain.get((venue, stripped_lower))
            if hit is not None:
                return hit

        # 4) Identity: if the raw symbol matches a canonical ID, return it
        if sym in ASSET_REGISTRY:
            return sym
        sym_upper = sym.upper()
        if sym_upper in ASSET_REGISTRY:
            return sym_upper

        # 5) Fallback: check dynamic token universe (top-500, etc.)
        fallback = self._fallback_lookup or _global_fallback
        if fallback is not None:
            hit = fallback(sym)
            if hit is not None:
                return hit

        return None

    def normalize_or_passthrough(
        self,
        venue: Venue,
        raw_symbol: str,
        chain: Chain | None = None,
    ) -> str:
        """Like :meth:`normalize` but returns *raw_symbol* when unresolved."""
        return self.normalize(venue, raw_symbol, chain) or raw_symbol

    def register_mapping(
        self,
        venue: Venue,
        venue_symbol: str,
        canonical_id: str,
        chain: Chain | None = None,
        is_contract_address: bool = False,
        notes: str | None = None,
    ) -> None:
        """Register a new mapping at runtime (e.g. discovered during ingestion)."""
        mapping = VenueAssetMapping(
            venue=venue,
            venue_symbol=venue_symbol,
            canonical_id=canonical_id,
            chain=chain,
            is_contract_address=is_contract_address,
            notes=notes,
        )
        self._index(mapping)

    def get_venue_symbols(
        self,
        canonical_id: str,
        venue: Venue | None = None,
    ) -> list[VenueAssetMapping]:
        """Reverse lookup: what does each venue call this canonical asset?

        Optionally filter to a single venue.
        """
        mappings = self._reverse.get(canonical_id, [])
        if venue is not None:
            return [m for m in mappings if m.venue == venue]
        return list(mappings)

    # -- heuristic detectors -------------------------------------------------

    @staticmethod
    def is_amm_lp(venue: Venue, raw_symbol: str) -> bool:
        """Heuristic: does *raw_symbol* look like an AMM LP token?"""
        for pat in _LP_PATTERNS:
            if pat.search(raw_symbol):
                return True
        return False

    @staticmethod
    def is_pendle(venue: Venue, raw_symbol: str) -> tuple[bool, str | None]:
        """Detect Pendle PT/YT tokens.

        Returns ``(True, "PT")`` or ``(True, "YT")`` if matched,
        ``(False, None)`` otherwise.
        """
        if _PENDLE_PT_RE.match(raw_symbol.strip()):
            return (True, "PT")
        if _PENDLE_YT_RE.match(raw_symbol.strip()):
            return (True, "YT")
        return (False, None)
