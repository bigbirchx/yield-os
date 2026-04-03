"""
Asset conversion graph for Yield OS.

Models the cost, time, and mechanics of converting between related assets
within the same umbrella group.  The route optimizer uses this to evaluate
multi-hop sourcing strategies (e.g.  hold ETH → wrap to WETH → stake to
wstETH → deposit into Aave).

All cost figures are *approximate defaults* suitable for desk-level
estimation.  They will eventually be replaced with live gas-price and
liquidity lookups, but static values are correct enough for routing
decisions today.

Slippage convention
-------------------
``slippage_bps_estimate`` is the expected mid-to-execution slippage for a
**$1 M notional** trade.  For larger sizes the router applies a simple
square-root scaling factor (see :meth:`ConversionRouter.estimate_conversion_cost`).

Usage::

    from asset_registry.conversions import ConversionRouter

    router = ConversionRouter()
    paths  = router.find_conversion_path("ETH", "wstETH")
    best   = router.cheapest_path("ETH", "wstETH", amount_usd=5_000_000)
"""
from __future__ import annotations

import math
from collections import defaultdict, deque
from enum import Enum

from pydantic import BaseModel

from .taxonomy import Chain


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ConversionMethod(str, Enum):
    """How two assets are converted."""

    WRAP = "WRAP"
    UNWRAP = "UNWRAP"
    STAKE = "STAKE"
    UNSTAKE_INSTANT = "UNSTAKE_INSTANT"
    UNSTAKE_QUEUE = "UNSTAKE_QUEUE"
    DEX_SWAP = "DEX_SWAP"
    BRIDGE = "BRIDGE"
    MINT = "MINT"
    REDEEM = "REDEEM"
    SAVINGS_DEPOSIT = "SAVINGS_DEPOSIT"
    SAVINGS_WITHDRAW = "SAVINGS_WITHDRAW"


# ---------------------------------------------------------------------------
# Edge model
# ---------------------------------------------------------------------------


class ConversionEdge(BaseModel):
    """One directed conversion between two canonical assets."""

    from_asset: str
    to_asset: str
    method: ConversionMethod
    chain: Chain
    protocol: str | None = None
    estimated_gas_usd: float = 0.0
    fee_bps: float = 0.0
    slippage_bps_estimate: float = 0.0
    min_duration_seconds: int = 0
    max_duration_seconds: int = 0
    is_deterministic: bool = True
    capacity_limited: bool = False
    notes: str | None = None

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Time constants (seconds)
# ---------------------------------------------------------------------------

_INSTANT = 0
_SECONDS = 1
_MINUTES = 60
_HOURS = 3600
_DAYS = 86400
_EPOCH_SOL = 2 * _DAYS  # ~2 days per Solana epoch


# ---------------------------------------------------------------------------
# Static conversion graph
# ---------------------------------------------------------------------------

_C = Chain
_M = ConversionMethod


def _e(
    from_asset: str,
    to_asset: str,
    method: ConversionMethod,
    chain: Chain,
    *,
    protocol: str | None = None,
    gas: float = 0.0,
    fee_bps: float = 0.0,
    slip_bps: float = 0.0,
    min_sec: int = 0,
    max_sec: int = 0,
    deterministic: bool = True,
    capacity_limited: bool = False,
    notes: str | None = None,
) -> ConversionEdge:
    return ConversionEdge(
        from_asset=from_asset,
        to_asset=to_asset,
        method=method,
        chain=chain,
        protocol=protocol,
        estimated_gas_usd=gas,
        fee_bps=fee_bps,
        slippage_bps_estimate=slip_bps,
        min_duration_seconds=min_sec,
        max_duration_seconds=max_sec,
        is_deterministic=deterministic,
        capacity_limited=capacity_limited,
        notes=notes,
    )


CONVERSION_GRAPH: list[ConversionEdge] = [
    # ══════════════════════════════════════════════════════════════════════
    # ETH Umbrella
    # ══════════════════════════════════════════════════════════════════════

    # ── ETH <-> WETH ─────────────────────────────────────────────────────
    _e("ETH", "WETH", _M.WRAP, _C.ETHEREUM, gas=0.50),
    _e("WETH", "ETH", _M.UNWRAP, _C.ETHEREUM, gas=0.50),

    # ── Lido: stETH / wstETH ────────────────────────────────────────────
    _e("ETH", "stETH", _M.STAKE, _C.ETHEREUM, protocol="lido", gas=2.0),
    _e("stETH", "ETH", _M.UNSTAKE_QUEUE, _C.ETHEREUM, protocol="lido",
       gas=2.0, min_sec=1 * _DAYS, max_sec=5 * _DAYS,
       deterministic=False, notes="Lido withdrawal queue; 1-5 day wait"),
    _e("stETH", "ETH", _M.DEX_SWAP, _C.ETHEREUM, protocol="curve",
       gas=5.0, fee_bps=1.0, slip_bps=3.0, deterministic=False),
    _e("stETH", "wstETH", _M.WRAP, _C.ETHEREUM, protocol="lido",
       gas=1.0, notes="Rate-based wrapping, not 1:1"),
    _e("wstETH", "stETH", _M.UNWRAP, _C.ETHEREUM, protocol="lido", gas=1.0),

    # ── Coinbase: cbETH ──────────────────────────────────────────────────
    _e("ETH", "cbETH", _M.STAKE, _C.ETHEREUM, protocol="coinbase", gas=2.0),
    _e("cbETH", "ETH", _M.DEX_SWAP, _C.ETHEREUM, protocol="uniswap",
       gas=5.0, fee_bps=30.0, slip_bps=4.0, deterministic=False),

    # ── Rocket Pool: rETH ────────────────────────────────────────────────
    _e("ETH", "rETH", _M.STAKE, _C.ETHEREUM, protocol="rocketpool",
       gas=5.0, fee_bps=5.0, capacity_limited=True,
       notes="Deposit pool must have capacity"),
    _e("rETH", "ETH", _M.DEX_SWAP, _C.ETHEREUM, protocol="uniswap",
       gas=5.0, fee_bps=30.0, slip_bps=4.0, deterministic=False),

    # ── ether.fi: eETH / weETH ──────────────────────────────────────────
    _e("ETH", "eETH", _M.STAKE, _C.ETHEREUM, protocol="etherfi", gas=3.0),
    _e("eETH", "weETH", _M.WRAP, _C.ETHEREUM, protocol="etherfi", gas=1.0),
    _e("weETH", "eETH", _M.UNWRAP, _C.ETHEREUM, protocol="etherfi", gas=1.0),
    _e("eETH", "ETH", _M.UNSTAKE_QUEUE, _C.ETHEREUM, protocol="etherfi",
       gas=2.0, min_sec=2 * _DAYS, max_sec=7 * _DAYS,
       deterministic=False, notes="ether.fi withdrawal queue"),

    # ── Mantle: mETH ────────────────────────────────────────────────────
    _e("ETH", "mETH", _M.STAKE, _C.ETHEREUM, protocol="mantle", gas=3.0),

    # ── Swell: swETH ────────────────────────────────────────────────────
    _e("ETH", "swETH", _M.STAKE, _C.ETHEREUM, protocol="swell", gas=3.0),

    # ══════════════════════════════════════════════════════════════════════
    # USD Umbrella
    # ══════════════════════════════════════════════════════════════════════

    # ── Tier-1 stablecoin swaps (Curve 3pool-like) ──────────────────────
    _e("USDC", "USDT", _M.DEX_SWAP, _C.ETHEREUM, protocol="curve",
       gas=3.0, fee_bps=1.0, slip_bps=1.0, deterministic=False),
    _e("USDT", "USDC", _M.DEX_SWAP, _C.ETHEREUM, protocol="curve",
       gas=3.0, fee_bps=1.0, slip_bps=1.0, deterministic=False),
    _e("USDC", "DAI", _M.DEX_SWAP, _C.ETHEREUM, protocol="curve",
       gas=3.0, fee_bps=1.0, slip_bps=1.0, deterministic=False),

    # ── MakerDAO / Sky: DAI <-> sDAI, USDS <-> sUSDS ────────────────────
    _e("DAI", "sDAI", _M.SAVINGS_DEPOSIT, _C.ETHEREUM, protocol="maker", gas=2.0),
    _e("sDAI", "DAI", _M.SAVINGS_WITHDRAW, _C.ETHEREUM, protocol="maker", gas=2.0),
    _e("USDS", "sUSDS", _M.SAVINGS_DEPOSIT, _C.ETHEREUM, protocol="sky", gas=2.0),
    _e("sUSDS", "USDS", _M.SAVINGS_WITHDRAW, _C.ETHEREUM, protocol="sky", gas=2.0),

    # ── DAI <-> USDS upgrade ────────────────────────────────────────────
    _e("DAI", "USDS", _M.WRAP, _C.ETHEREUM, protocol="sky",
       gas=1.0, notes="DAI to USDS upgrade via Sky"),
    _e("USDS", "DAI", _M.UNWRAP, _C.ETHEREUM, protocol="sky",
       gas=1.0, notes="USDS to DAI downgrade via Sky"),

    # ── Ethena: USDe / sUSDe ────────────────────────────────────────────
    _e("USDC", "USDe", _M.MINT, _C.ETHEREUM, protocol="ethena",
       gas=5.0, capacity_limited=True,
       notes="Minting available when Ethena minting is open"),
    _e("USDe", "USDC", _M.REDEEM, _C.ETHEREUM, protocol="ethena",
       gas=5.0, min_sec=7 * _DAYS, max_sec=7 * _DAYS,
       deterministic=False, notes="7-day cooldown period"),
    _e("USDe", "sUSDe", _M.STAKE, _C.ETHEREUM, protocol="ethena", gas=2.0),
    _e("sUSDe", "USDe", _M.UNSTAKE_QUEUE, _C.ETHEREUM, protocol="ethena",
       gas=2.0, min_sec=7 * _DAYS, max_sec=7 * _DAYS,
       deterministic=False, notes="7-day cooldown period"),

    # ══════════════════════════════════════════════════════════════════════
    # BTC Umbrella
    # ══════════════════════════════════════════════════════════════════════

    # ── BitGo: BTC <-> WBTC ─────────────────────────────────────────────
    _e("BTC", "WBTC", _M.WRAP, _C.ETHEREUM, protocol="bitgo",
       gas=3.0, fee_bps=17.5, min_sec=2 * _HOURS, max_sec=2 * _HOURS,
       capacity_limited=True, deterministic=False,
       notes="Custodial minting via BitGo; 10-25 bps fee"),
    _e("WBTC", "BTC", _M.UNWRAP, _C.ETHEREUM, protocol="bitgo",
       gas=3.0, fee_bps=17.5, min_sec=2 * _HOURS, max_sec=2 * _HOURS,
       capacity_limited=True, deterministic=False,
       notes="Custodial redemption via BitGo; 10-25 bps fee"),

    # ── DEX swaps within BTC family ─────────────────────────────────────
    _e("WBTC", "cbBTC", _M.DEX_SWAP, _C.ETHEREUM, protocol="uniswap",
       gas=5.0, fee_bps=30.0, slip_bps=4.0, deterministic=False),
    _e("WBTC", "tBTC", _M.DEX_SWAP, _C.ETHEREUM, protocol="curve",
       gas=5.0, fee_bps=4.0, slip_bps=4.0, deterministic=False),

    # ══════════════════════════════════════════════════════════════════════
    # SOL Umbrella
    # ══════════════════════════════════════════════════════════════════════

    # ── Marinade: mSOL ──────────────────────────────────────────────────
    _e("SOL", "mSOL", _M.STAKE, _C.SOLANA, protocol="marinade", gas=0.01),
    _e("mSOL", "SOL", _M.UNSTAKE_QUEUE, _C.SOLANA, protocol="marinade",
       gas=0.01, min_sec=_EPOCH_SOL, max_sec=_EPOCH_SOL,
       notes="1 Solana epoch (~2 days)"),
    _e("mSOL", "SOL", _M.DEX_SWAP, _C.SOLANA, protocol="jupiter",
       gas=0.01, fee_bps=7.0, slip_bps=3.0, deterministic=False),

    # ── Jito: jitoSOL ──────────────────────────────────────────────────
    _e("SOL", "jitoSOL", _M.STAKE, _C.SOLANA, protocol="jito", gas=0.01),
    _e("jitoSOL", "SOL", _M.UNSTAKE_QUEUE, _C.SOLANA, protocol="jito",
       gas=0.01, min_sec=_EPOCH_SOL, max_sec=_EPOCH_SOL,
       notes="1 Solana epoch (~2 days)"),
    _e("jitoSOL", "SOL", _M.DEX_SWAP, _C.SOLANA, protocol="jupiter",
       gas=0.01, fee_bps=7.0, slip_bps=3.0, deterministic=False),

    # ── BlazeStake: bSOL ────────────────────────────────────────────────
    _e("SOL", "bSOL", _M.STAKE, _C.SOLANA, protocol="blaze", gas=0.01),

    # ── Sanctum: INF ────────────────────────────────────────────────────
    _e("SOL", "INF", _M.STAKE, _C.SOLANA, protocol="sanctum", gas=0.01),

    # ══════════════════════════════════════════════════════════════════════
    # Cross-chain Bridging
    # ══════════════════════════════════════════════════════════════════════

    _e("WETH", "WETH", _M.BRIDGE, _C.ARBITRUM, protocol="arbitrum-native",
       gas=5.0, min_sec=10 * _MINUTES, max_sec=7 * _DAYS,
       deterministic=False,
       notes="Fast bridges ~10 min; native bridge 7-day challenge period"),
    _e("USDC", "USDC", _M.BRIDGE, _C.ARBITRUM, protocol="cctp",
       gas=5.0, min_sec=15 * _MINUTES, max_sec=15 * _MINUTES,
       notes="Circle CCTP burn-and-mint; Ethereum -> Arbitrum"),
    _e("USDC", "USDC", _M.BRIDGE, _C.BASE, protocol="cctp",
       gas=3.0, min_sec=15 * _MINUTES, max_sec=15 * _MINUTES,
       notes="Circle CCTP burn-and-mint; Ethereum -> Base"),
]


# ---------------------------------------------------------------------------
# Slippage reference notional — base estimates are for this trade size.
# ---------------------------------------------------------------------------

_SLIPPAGE_REFERENCE_USD = 1_000_000.0


# ---------------------------------------------------------------------------
# Conversion router
# ---------------------------------------------------------------------------


class ConversionRouter:
    """Graph-based conversion path finder and cost estimator.

    Builds an adjacency list from :data:`CONVERSION_GRAPH` on construction
    for O(1) neighbour lookups.
    """

    def __init__(self, edges: list[ConversionEdge] | None = None) -> None:
        self._edges = list(edges or CONVERSION_GRAPH)

        # adjacency: from_asset -> list[ConversionEdge]
        self._adj: dict[str, list[ConversionEdge]] = defaultdict(list)
        for edge in self._edges:
            self._adj[edge.from_asset].append(edge)

    # -- direct lookups ------------------------------------------------------

    def find_direct_conversions(
        self,
        from_asset: str,
        to_asset: str,
        chain: Chain | None = None,
    ) -> list[ConversionEdge]:
        """Return all single-hop edges from *from_asset* to *to_asset*.

        Optionally filter to a specific chain.
        """
        edges = [
            e for e in self._adj.get(from_asset, [])
            if e.to_asset == to_asset
        ]
        if chain is not None:
            edges = [e for e in edges if e.chain == chain]
        return edges

    # -- multi-hop path search -----------------------------------------------

    def find_conversion_path(
        self,
        from_asset: str,
        to_asset: str,
        chain: Chain | None = None,
        max_hops: int = 3,
    ) -> list[list[ConversionEdge]]:
        """BFS over the conversion graph up to *max_hops* edges.

        Returns every acyclic path from *from_asset* to *to_asset*.
        Paths are ordered shortest-first.

        When *chain* is given, only edges on that chain are traversed.
        """
        if from_asset == to_asset:
            return [[]]

        results: list[list[ConversionEdge]] = []

        # BFS queue entries: (current_asset, path_so_far, visited_assets)
        queue: deque[tuple[str, list[ConversionEdge], set[str]]] = deque()
        queue.append((from_asset, [], {from_asset}))

        while queue:
            current, path, visited = queue.popleft()

            if len(path) >= max_hops:
                continue

            for edge in self._adj.get(current, []):
                if chain is not None and edge.chain != chain:
                    continue
                if edge.to_asset in visited:
                    continue

                new_path = path + [edge]

                if edge.to_asset == to_asset:
                    results.append(new_path)
                    continue

                new_visited = visited | {edge.to_asset}
                queue.append((edge.to_asset, new_path, new_visited))

        return results

    # -- cost estimation -----------------------------------------------------

    @staticmethod
    def _scale_slippage(base_bps: float, amount_usd: float) -> float:
        """Scale slippage from the reference notional to the actual size.

        Uses square-root scaling: slippage grows with sqrt(size / reference).
        This is a standard market-microstructure approximation — price impact
        scales roughly with the square root of order size.

        For deterministic conversions (base_bps == 0) returns 0.
        """
        if base_bps == 0.0 or amount_usd <= 0.0:
            return 0.0
        ratio = amount_usd / _SLIPPAGE_REFERENCE_USD
        return base_bps * math.sqrt(ratio)

    def estimate_conversion_cost(
        self,
        path: list[ConversionEdge],
        amount_usd: float,
    ) -> dict:
        """Estimate the total cost of traversing *path* for *amount_usd*.

        Returns a dict with:
        - ``total_gas_usd``
        - ``total_fee_bps``
        - ``total_slippage_bps``
        - ``total_cost_bps``   (fee + slippage, excludes gas)
        - ``net_cost_usd``     (gas + bps costs converted to USD)
        - ``min_duration_seconds``
        - ``max_duration_seconds``
        - ``num_hops``
        - ``is_deterministic``
        - ``has_capacity_limit``
        """
        total_gas = 0.0
        total_fee_bps = 0.0
        total_slip_bps = 0.0
        min_dur = 0
        max_dur = 0
        deterministic = True
        capacity_limited = False

        for edge in path:
            total_gas += edge.estimated_gas_usd
            total_fee_bps += edge.fee_bps
            total_slip_bps += self._scale_slippage(
                edge.slippage_bps_estimate, amount_usd,
            )
            min_dur += edge.min_duration_seconds
            max_dur += edge.max_duration_seconds
            if not edge.is_deterministic:
                deterministic = False
            if edge.capacity_limited:
                capacity_limited = True

        total_bps = total_fee_bps + total_slip_bps
        bps_cost_usd = amount_usd * total_bps / 10_000.0
        net_cost_usd = total_gas + bps_cost_usd

        return {
            "total_gas_usd": round(total_gas, 2),
            "total_fee_bps": round(total_fee_bps, 2),
            "total_slippage_bps": round(total_slip_bps, 2),
            "total_cost_bps": round(total_bps, 2),
            "net_cost_usd": round(net_cost_usd, 2),
            "min_duration_seconds": min_dur,
            "max_duration_seconds": max_dur,
            "num_hops": len(path),
            "is_deterministic": deterministic,
            "has_capacity_limit": capacity_limited,
        }

    # -- cheapest path -------------------------------------------------------

    def cheapest_path(
        self,
        from_asset: str,
        to_asset: str,
        amount_usd: float,
        chain: Chain | None = None,
        max_hops: int = 3,
    ) -> tuple[list[ConversionEdge], dict] | None:
        """Find the path with the lowest ``net_cost_usd``.

        Returns ``(path, cost_estimate)`` or ``None`` if no path exists.
        """
        paths = self.find_conversion_path(
            from_asset, to_asset, chain=chain, max_hops=max_hops,
        )
        if not paths:
            return None

        best_path: list[ConversionEdge] | None = None
        best_cost: dict | None = None
        best_net: float = float("inf")

        for path in paths:
            cost = self.estimate_conversion_cost(path, amount_usd)
            if cost["net_cost_usd"] < best_net:
                best_net = cost["net_cost_usd"]
                best_path = path
                best_cost = cost

        if best_path is None or best_cost is None:
            return None
        return (best_path, best_cost)
