"""
Canonical asset taxonomy for Yield OS.

This module is the single source of truth for how every crypto asset is
classified, grouped, and related.  It has zero framework dependencies —
only Python stdlib + Pydantic for validation.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AssetUmbrella(str, Enum):
    """Top-level economic grouping."""

    USD = "USD"
    ETH = "ETH"
    BTC = "BTC"
    SOL = "SOL"
    HYPE = "HYPE"
    OTHER = "OTHER"


class AssetSubType(str, Enum):
    """Structural classification within an umbrella."""

    NATIVE = "NATIVE"
    WRAPPED_NATIVE = "WRAPPED_NATIVE"
    BRIDGED_NATIVE = "BRIDGED_NATIVE"
    LIQUID_STAKING_TOKEN = "LIQUID_STAKING_TOKEN"
    LIQUID_RESTAKING_TOKEN = "LIQUID_RESTAKING_TOKEN"
    TIER1_STABLE = "TIER1_STABLE"
    TIER2_STABLE = "TIER2_STABLE"
    TOKENIZED_YIELD_STRATEGY = "TOKENIZED_YIELD_STRATEGY"
    SAVINGS_WRAPPER = "SAVINGS_WRAPPER"
    RECEIPT_TOKEN = "RECEIPT_TOKEN"
    PENDLE_PT = "PENDLE_PT"
    PENDLE_YT = "PENDLE_YT"
    AMM_LP = "AMM_LP"
    NATIVE_TOKEN = "NATIVE_TOKEN"


class FungibilityTier(str, Enum):
    """How easily two assets within the same umbrella can be exchanged."""

    FULLY_FUNGIBLE = "FULLY_FUNGIBLE"
    CONVERTIBLE = "CONVERTIBLE"
    RELATED = "RELATED"


class Chain(str, Enum):
    """Supported blockchain networks."""

    ETHEREUM = "ETHEREUM"
    ARBITRUM = "ARBITRUM"
    OPTIMISM = "OPTIMISM"
    BASE = "BASE"
    POLYGON = "POLYGON"
    AVALANCHE = "AVALANCHE"
    BSC = "BSC"
    SOLANA = "SOLANA"
    TRON = "TRON"
    SUI = "SUI"
    APTOS = "APTOS"
    SEI = "SEI"
    HYPERLIQUID = "HYPERLIQUID"
    MANTLE = "MANTLE"
    SCROLL = "SCROLL"
    LINEA = "LINEA"
    BLAST = "BLAST"
    MODE = "MODE"
    MANTA = "MANTA"
    ZKSYNC = "ZKSYNC"


# ---------------------------------------------------------------------------
# Asset definition model
# ---------------------------------------------------------------------------


class AssetDefinition(BaseModel):
    """Immutable descriptor for a single asset known to Yield OS."""

    canonical_id: str
    name: str
    umbrella: AssetUmbrella
    sub_type: AssetSubType
    fungibility: FungibilityTier
    native_chains: list[Chain]
    coingecko_id: str | None = None
    decimals_by_chain: dict[Chain, int] = {}
    underlying_asset_id: str | None = None
    tags: list[str] = []

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Master registry
# ---------------------------------------------------------------------------

_C = Chain

ASSET_REGISTRY: dict[str, AssetDefinition] = {}


def _r(
    canonical_id: str,
    name: str,
    umbrella: AssetUmbrella,
    sub_type: AssetSubType,
    fungibility: FungibilityTier,
    native_chains: list[Chain] | None = None,
    coingecko_id: str | None = None,
    decimals_by_chain: dict[Chain, int] | None = None,
    underlying_asset_id: str | None = None,
    tags: list[str] | None = None,
) -> None:
    """Register an asset definition in the global registry."""
    ASSET_REGISTRY[canonical_id] = AssetDefinition(
        canonical_id=canonical_id,
        name=name,
        umbrella=umbrella,
        sub_type=sub_type,
        fungibility=fungibility,
        native_chains=native_chains or [],
        coingecko_id=coingecko_id,
        decimals_by_chain=decimals_by_chain or {},
        underlying_asset_id=underlying_asset_id,
        tags=tags or [],
    )


# ── USD Umbrella ──────────────────────────────────────────────────────────

_r(
    "USD", "US Dollar", AssetUmbrella.USD, AssetSubType.NATIVE,
    FungibilityTier.FULLY_FUNGIBLE,
)

_r(
    "USDC", "USD Coin", AssetUmbrella.USD, AssetSubType.TIER1_STABLE,
    FungibilityTier.FULLY_FUNGIBLE,
    native_chains=[_C.ETHEREUM, _C.ARBITRUM, _C.OPTIMISM, _C.BASE, _C.POLYGON, _C.AVALANCHE, _C.SOLANA],
    coingecko_id="usd-coin",
    decimals_by_chain={_C.ETHEREUM: 6, _C.ARBITRUM: 6, _C.OPTIMISM: 6, _C.BASE: 6, _C.POLYGON: 6, _C.AVALANCHE: 6, _C.SOLANA: 6},
)

_r(
    "USDT", "Tether", AssetUmbrella.USD, AssetSubType.TIER1_STABLE,
    FungibilityTier.FULLY_FUNGIBLE,
    native_chains=[_C.ETHEREUM, _C.TRON, _C.ARBITRUM, _C.OPTIMISM, _C.BSC, _C.AVALANCHE, _C.SOLANA, _C.POLYGON],
    coingecko_id="tether",
    decimals_by_chain={_C.ETHEREUM: 6, _C.TRON: 6, _C.ARBITRUM: 6, _C.OPTIMISM: 6, _C.BSC: 18, _C.AVALANCHE: 6, _C.SOLANA: 6, _C.POLYGON: 6},
)

_r(
    "DAI", "Dai", AssetUmbrella.USD, AssetSubType.TIER1_STABLE,
    FungibilityTier.FULLY_FUNGIBLE,
    native_chains=[_C.ETHEREUM],
    coingecko_id="dai",
    decimals_by_chain={_C.ETHEREUM: 18},
)

_r(
    "USDS", "USDS", AssetUmbrella.USD, AssetSubType.TIER1_STABLE,
    FungibilityTier.FULLY_FUNGIBLE,
    native_chains=[_C.ETHEREUM],
    coingecko_id="usds",
    decimals_by_chain={_C.ETHEREUM: 18},
    underlying_asset_id="DAI",
)

_r(
    "PYUSD", "PayPal USD", AssetUmbrella.USD, AssetSubType.TIER2_STABLE,
    FungibilityTier.RELATED,
    native_chains=[_C.ETHEREUM, _C.SOLANA],
    coingecko_id="paypal-usd",
    decimals_by_chain={_C.ETHEREUM: 6, _C.SOLANA: 6},
)

_r(
    "RLUSD", "Ripple USD", AssetUmbrella.USD, AssetSubType.TIER2_STABLE,
    FungibilityTier.RELATED,
    native_chains=[_C.ETHEREUM],
    coingecko_id="ripple-usd",
    decimals_by_chain={_C.ETHEREUM: 18},
)

_r(
    "USD1", "USD1", AssetUmbrella.USD, AssetSubType.TIER2_STABLE,
    FungibilityTier.RELATED,
    native_chains=[_C.ETHEREUM],
)

_r(
    "FDUSD", "First Digital USD", AssetUmbrella.USD, AssetSubType.TIER2_STABLE,
    FungibilityTier.RELATED,
    native_chains=[_C.ETHEREUM, _C.BSC],
    coingecko_id="first-digital-usd",
    decimals_by_chain={_C.ETHEREUM: 18, _C.BSC: 18},
)

_r(
    "TUSD", "TrueUSD", AssetUmbrella.USD, AssetSubType.TIER2_STABLE,
    FungibilityTier.RELATED,
    native_chains=[_C.ETHEREUM, _C.BSC, _C.TRON],
    coingecko_id="true-usd",
    decimals_by_chain={_C.ETHEREUM: 18, _C.BSC: 18, _C.TRON: 18},
)

_r(
    "USDP", "Pax Dollar", AssetUmbrella.USD, AssetSubType.TIER2_STABLE,
    FungibilityTier.RELATED,
    native_chains=[_C.ETHEREUM],
    coingecko_id="paxos-standard",
    decimals_by_chain={_C.ETHEREUM: 18},
)

_r(
    "GUSD", "Gemini Dollar", AssetUmbrella.USD, AssetSubType.TIER2_STABLE,
    FungibilityTier.RELATED,
    native_chains=[_C.ETHEREUM],
    coingecko_id="gemini-dollar",
    decimals_by_chain={_C.ETHEREUM: 2},
)

_r(
    "USDe", "Ethena USDe", AssetUmbrella.USD, AssetSubType.TOKENIZED_YIELD_STRATEGY,
    FungibilityTier.RELATED,
    native_chains=[_C.ETHEREUM],
    coingecko_id="ethena-usde",
    decimals_by_chain={_C.ETHEREUM: 18},
    tags=["ethena", "delta-neutral"],
)

_r(
    "sUSDe", "Staked USDe", AssetUmbrella.USD, AssetSubType.TOKENIZED_YIELD_STRATEGY,
    FungibilityTier.RELATED,
    native_chains=[_C.ETHEREUM],
    coingecko_id="ethena-staked-usde",
    decimals_by_chain={_C.ETHEREUM: 18},
    underlying_asset_id="USDe",
    tags=["ethena"],
)

_r(
    "sDAI", "Savings Dai", AssetUmbrella.USD, AssetSubType.SAVINGS_WRAPPER,
    FungibilityTier.CONVERTIBLE,
    native_chains=[_C.ETHEREUM],
    coingecko_id="savings-dai",
    decimals_by_chain={_C.ETHEREUM: 18},
    underlying_asset_id="DAI",
)

_r(
    "sUSDS", "Savings USDS", AssetUmbrella.USD, AssetSubType.SAVINGS_WRAPPER,
    FungibilityTier.CONVERTIBLE,
    native_chains=[_C.ETHEREUM],
    decimals_by_chain={_C.ETHEREUM: 18},
    underlying_asset_id="USDS",
)

_r(
    "FRAX", "Frax", AssetUmbrella.USD, AssetSubType.TIER2_STABLE,
    FungibilityTier.RELATED,
    native_chains=[_C.ETHEREUM],
    coingecko_id="frax",
    decimals_by_chain={_C.ETHEREUM: 18},
)

_r(
    "crvUSD", "Curve USD", AssetUmbrella.USD, AssetSubType.TIER2_STABLE,
    FungibilityTier.RELATED,
    native_chains=[_C.ETHEREUM],
    coingecko_id="crvusd",
    decimals_by_chain={_C.ETHEREUM: 18},
)

_r(
    "GHO", "GHO", AssetUmbrella.USD, AssetSubType.TIER2_STABLE,
    FungibilityTier.RELATED,
    native_chains=[_C.ETHEREUM],
    coingecko_id="gho",
    decimals_by_chain={_C.ETHEREUM: 18},
    tags=["aave"],
)

_r(
    "DOLA", "Dola USD", AssetUmbrella.USD, AssetSubType.TIER2_STABLE,
    FungibilityTier.RELATED,
    native_chains=[_C.ETHEREUM],
    coingecko_id="dola-usd",
    decimals_by_chain={_C.ETHEREUM: 18},
)


# ── ETH Umbrella ──────────────────────────────────────────────────────────

_r(
    "ETH", "Ether", AssetUmbrella.ETH, AssetSubType.NATIVE,
    FungibilityTier.FULLY_FUNGIBLE,
    native_chains=[_C.ETHEREUM],
    coingecko_id="ethereum",
    decimals_by_chain={_C.ETHEREUM: 18},
)

_r(
    "WETH", "Wrapped Ether", AssetUmbrella.ETH, AssetSubType.WRAPPED_NATIVE,
    FungibilityTier.FULLY_FUNGIBLE,
    native_chains=[_C.ETHEREUM, _C.ARBITRUM, _C.OPTIMISM, _C.BASE, _C.POLYGON],
    coingecko_id="weth",
    decimals_by_chain={_C.ETHEREUM: 18, _C.ARBITRUM: 18, _C.OPTIMISM: 18, _C.BASE: 18, _C.POLYGON: 18},
    underlying_asset_id="ETH",
)

_r(
    "stETH", "Lido Staked Ether", AssetUmbrella.ETH, AssetSubType.LIQUID_STAKING_TOKEN,
    FungibilityTier.CONVERTIBLE,
    native_chains=[_C.ETHEREUM],
    coingecko_id="staked-ether",
    decimals_by_chain={_C.ETHEREUM: 18},
    underlying_asset_id="ETH",
    tags=["lido"],
)

_r(
    "wstETH", "Wrapped stETH", AssetUmbrella.ETH, AssetSubType.LIQUID_STAKING_TOKEN,
    FungibilityTier.CONVERTIBLE,
    native_chains=[_C.ETHEREUM, _C.ARBITRUM, _C.OPTIMISM, _C.BASE],
    coingecko_id="wrapped-steth",
    decimals_by_chain={_C.ETHEREUM: 18, _C.ARBITRUM: 18, _C.OPTIMISM: 18, _C.BASE: 18},
    underlying_asset_id="stETH",
    tags=["lido"],
)

_r(
    "cbETH", "Coinbase Wrapped Staked ETH", AssetUmbrella.ETH, AssetSubType.LIQUID_STAKING_TOKEN,
    FungibilityTier.CONVERTIBLE,
    native_chains=[_C.ETHEREUM, _C.BASE],
    coingecko_id="coinbase-wrapped-staked-eth",
    decimals_by_chain={_C.ETHEREUM: 18, _C.BASE: 18},
    underlying_asset_id="ETH",
    tags=["coinbase"],
)

_r(
    "rETH", "Rocket Pool ETH", AssetUmbrella.ETH, AssetSubType.LIQUID_STAKING_TOKEN,
    FungibilityTier.CONVERTIBLE,
    native_chains=[_C.ETHEREUM, _C.ARBITRUM, _C.OPTIMISM],
    coingecko_id="rocket-pool-eth",
    decimals_by_chain={_C.ETHEREUM: 18, _C.ARBITRUM: 18, _C.OPTIMISM: 18},
    underlying_asset_id="ETH",
    tags=["rocketpool"],
)

_r(
    "mETH", "Mantle Staked Ether", AssetUmbrella.ETH, AssetSubType.LIQUID_STAKING_TOKEN,
    FungibilityTier.CONVERTIBLE,
    native_chains=[_C.ETHEREUM],
    coingecko_id="mantle-staked-ether",
    decimals_by_chain={_C.ETHEREUM: 18},
    underlying_asset_id="ETH",
    tags=["mantle"],
)

_r(
    "swETH", "Swell ETH", AssetUmbrella.ETH, AssetSubType.LIQUID_STAKING_TOKEN,
    FungibilityTier.CONVERTIBLE,
    native_chains=[_C.ETHEREUM],
    coingecko_id="sweth",
    decimals_by_chain={_C.ETHEREUM: 18},
    underlying_asset_id="ETH",
    tags=["swell"],
)

_r(
    "WBETH", "Wrapped Beacon ETH", AssetUmbrella.ETH, AssetSubType.LIQUID_STAKING_TOKEN,
    FungibilityTier.CONVERTIBLE,
    native_chains=[_C.ETHEREUM, _C.BSC],
    coingecko_id="wrapped-beacon-eth",
    decimals_by_chain={_C.ETHEREUM: 18, _C.BSC: 18},
    underlying_asset_id="ETH",
    tags=["binance"],
)

_r(
    "eETH", "ether.fi Staked ETH", AssetUmbrella.ETH, AssetSubType.LIQUID_RESTAKING_TOKEN,
    FungibilityTier.CONVERTIBLE,
    native_chains=[_C.ETHEREUM],
    coingecko_id="ether-fi-staked-eth",
    decimals_by_chain={_C.ETHEREUM: 18},
    underlying_asset_id="ETH",
    tags=["etherfi"],
)

_r(
    "weETH", "Wrapped eETH", AssetUmbrella.ETH, AssetSubType.LIQUID_RESTAKING_TOKEN,
    FungibilityTier.CONVERTIBLE,
    native_chains=[_C.ETHEREUM, _C.ARBITRUM, _C.BASE],
    coingecko_id="wrapped-eeth",
    decimals_by_chain={_C.ETHEREUM: 18, _C.ARBITRUM: 18, _C.BASE: 18},
    underlying_asset_id="eETH",
    tags=["etherfi"],
)

_r(
    "rsETH", "Kelp DAO Restaked ETH", AssetUmbrella.ETH, AssetSubType.LIQUID_RESTAKING_TOKEN,
    FungibilityTier.CONVERTIBLE,
    native_chains=[_C.ETHEREUM],
    coingecko_id="kelp-dao-restaked-eth",
    decimals_by_chain={_C.ETHEREUM: 18},
    underlying_asset_id="ETH",
    tags=["kelp"],
)

_r(
    "ezETH", "Renzo Restaked ETH", AssetUmbrella.ETH, AssetSubType.LIQUID_RESTAKING_TOKEN,
    FungibilityTier.CONVERTIBLE,
    native_chains=[_C.ETHEREUM],
    coingecko_id="renzo-restaked-eth",
    decimals_by_chain={_C.ETHEREUM: 18},
    underlying_asset_id="ETH",
    tags=["renzo"],
)

_r(
    "pufETH", "Puffer ETH", AssetUmbrella.ETH, AssetSubType.LIQUID_RESTAKING_TOKEN,
    FungibilityTier.CONVERTIBLE,
    native_chains=[_C.ETHEREUM],
    coingecko_id="puffer-finance",
    decimals_by_chain={_C.ETHEREUM: 18},
    underlying_asset_id="ETH",
    tags=["puffer"],
)


# ── BTC Umbrella ──────────────────────────────────────────────────────────

_r(
    "BTC", "Bitcoin", AssetUmbrella.BTC, AssetSubType.NATIVE,
    FungibilityTier.FULLY_FUNGIBLE,
    coingecko_id="bitcoin",
    decimals_by_chain={},
)

_r(
    "WBTC", "Wrapped Bitcoin", AssetUmbrella.BTC, AssetSubType.LIQUID_STAKING_TOKEN,
    FungibilityTier.CONVERTIBLE,
    native_chains=[_C.ETHEREUM],
    coingecko_id="wrapped-bitcoin",
    decimals_by_chain={_C.ETHEREUM: 8},
    underlying_asset_id="BTC",
    tags=["bitgo"],
)

_r(
    "cbBTC", "Coinbase Wrapped BTC", AssetUmbrella.BTC, AssetSubType.LIQUID_STAKING_TOKEN,
    FungibilityTier.CONVERTIBLE,
    native_chains=[_C.ETHEREUM, _C.BASE],
    coingecko_id="coinbase-wrapped-btc",
    decimals_by_chain={_C.ETHEREUM: 8, _C.BASE: 8},
    underlying_asset_id="BTC",
    tags=["coinbase"],
)

_r(
    "tBTC", "tBTC", AssetUmbrella.BTC, AssetSubType.LIQUID_STAKING_TOKEN,
    FungibilityTier.CONVERTIBLE,
    native_chains=[_C.ETHEREUM],
    coingecko_id="tbtc",
    decimals_by_chain={_C.ETHEREUM: 18},
    underlying_asset_id="BTC",
    tags=["threshold"],
)

_r(
    "LBTC", "Lombard Staked BTC", AssetUmbrella.BTC, AssetSubType.LIQUID_STAKING_TOKEN,
    FungibilityTier.CONVERTIBLE,
    native_chains=[_C.ETHEREUM],
    coingecko_id="lombard-staked-btc",
    decimals_by_chain={_C.ETHEREUM: 8},
    underlying_asset_id="BTC",
    tags=["lombard"],
)

_r(
    "sBTC", "sBTC", AssetUmbrella.BTC, AssetSubType.LIQUID_STAKING_TOKEN,
    FungibilityTier.CONVERTIBLE,
    native_chains=[_C.ETHEREUM],
    decimals_by_chain={_C.ETHEREUM: 18},
    underlying_asset_id="BTC",
)


# ── SOL Umbrella ──────────────────────────────────────────────────────────

_r(
    "SOL", "Solana", AssetUmbrella.SOL, AssetSubType.NATIVE,
    FungibilityTier.FULLY_FUNGIBLE,
    native_chains=[_C.SOLANA],
    coingecko_id="solana",
    decimals_by_chain={_C.SOLANA: 9},
)

_r(
    "mSOL", "Marinade Staked SOL", AssetUmbrella.SOL, AssetSubType.LIQUID_STAKING_TOKEN,
    FungibilityTier.CONVERTIBLE,
    native_chains=[_C.SOLANA],
    coingecko_id="msol",
    decimals_by_chain={_C.SOLANA: 9},
    underlying_asset_id="SOL",
    tags=["marinade"],
)

_r(
    "jitoSOL", "Jito Staked SOL", AssetUmbrella.SOL, AssetSubType.LIQUID_STAKING_TOKEN,
    FungibilityTier.CONVERTIBLE,
    native_chains=[_C.SOLANA],
    coingecko_id="jito-staked-sol",
    decimals_by_chain={_C.SOLANA: 9},
    underlying_asset_id="SOL",
    tags=["jito"],
)

_r(
    "bSOL", "BlazeStake Staked SOL", AssetUmbrella.SOL, AssetSubType.LIQUID_STAKING_TOKEN,
    FungibilityTier.CONVERTIBLE,
    native_chains=[_C.SOLANA],
    coingecko_id="blazestake-staked-sol",
    decimals_by_chain={_C.SOLANA: 9},
    underlying_asset_id="SOL",
    tags=["blaze"],
)

_r(
    "INF", "Infinity Staked SOL", AssetUmbrella.SOL, AssetSubType.LIQUID_STAKING_TOKEN,
    FungibilityTier.CONVERTIBLE,
    native_chains=[_C.SOLANA],
    coingecko_id="infinity-staked-sol",
    decimals_by_chain={_C.SOLANA: 9},
    underlying_asset_id="SOL",
    tags=["sanctum"],
)

_r(
    "JupSOL", "Jupiter Staked SOL", AssetUmbrella.SOL, AssetSubType.LIQUID_STAKING_TOKEN,
    FungibilityTier.CONVERTIBLE,
    native_chains=[_C.SOLANA],
    coingecko_id="jupiter-staked-sol",
    decimals_by_chain={_C.SOLANA: 9},
    underlying_asset_id="SOL",
    tags=["jupiter"],
)

_r(
    "JLP", "Jupiter Liquidity Pool", AssetUmbrella.OTHER, AssetSubType.RECEIPT_TOKEN,
    FungibilityTier.RELATED,
    native_chains=[_C.SOLANA],
    coingecko_id="jupiter-perpetuals-liquidity-provider-token",
    decimals_by_chain={_C.SOLANA: 6},
    tags=["jupiter", "perpetuals"],
)


# ── HYPE Umbrella ─────────────────────────────────────────────────────────

_r(
    "HYPE", "Hyperliquid", AssetUmbrella.HYPE, AssetSubType.NATIVE,
    FungibilityTier.FULLY_FUNGIBLE,
    native_chains=[_C.HYPERLIQUID],
    coingecko_id="hyperliquid",
)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def get_umbrella_assets(umbrella: AssetUmbrella) -> list[AssetDefinition]:
    """Return all assets belonging to the given umbrella group."""
    return [a for a in ASSET_REGISTRY.values() if a.umbrella == umbrella]


def get_fungible_group(canonical_id: str) -> list[str]:
    """Return all asset IDs that are FULLY_FUNGIBLE with *canonical_id*.

    Two assets are in the same fungible group when they share the same
    umbrella **and** both have ``FungibilityTier.FULLY_FUNGIBLE``.
    The query asset itself is always included in the result.
    """
    asset = ASSET_REGISTRY.get(canonical_id)
    if asset is None:
        return [canonical_id]
    if asset.fungibility != FungibilityTier.FULLY_FUNGIBLE:
        return [canonical_id]
    return [
        a.canonical_id
        for a in ASSET_REGISTRY.values()
        if a.umbrella == asset.umbrella
        and a.fungibility == FungibilityTier.FULLY_FUNGIBLE
    ]


def resolve_underlying_chain(canonical_id: str) -> str:
    """Walk up the ``underlying_asset_id`` chain to find the root asset.

    For example ``weETH -> eETH -> ETH``.  Returns the input unchanged
    if the asset has no underlying or is not in the registry.
    """
    seen: set[str] = set()
    current = canonical_id
    while current in ASSET_REGISTRY:
        if current in seen:
            break
        seen.add(current)
        underlying = ASSET_REGISTRY[current].underlying_asset_id
        if underlying is None:
            break
        current = underlying
    return current
