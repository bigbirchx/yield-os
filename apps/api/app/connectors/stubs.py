"""
Stub adapters for protocols not yet fully implemented.

Each stub class is a placeholder that:
  - Implements the full ProtocolAdapter interface
  - Has correct venue, chains, and protocol metadata
  - Returns an empty list from fetch_opportunities() with a log warning
  - Documents what API/subgraph to use when implementing for real

This module lets the system register all known venues at startup so that
the scheduler, health checks, and API discovery are aware of them,
even before full data ingestion is wired up.

To graduate a stub to a full adapter:
  1. Create a dedicated module (e.g., apps/api/app/connectors/venus.py)
  2. Implement fetch_opportunities() with real data fetching
  3. Replace the stub registration in opportunity_ingestion.py
"""
from __future__ import annotations

from typing import Any

import structlog

from app.connectors.base_adapter import ProtocolAdapter
from asset_registry import Chain, Venue
from opportunity_schema import MarketOpportunity

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Stub base class
# ---------------------------------------------------------------------------


class StubAdapter(ProtocolAdapter):
    """Base class for not-yet-implemented adapters.

    Subclasses only need to declare the protocol-identifying properties.
    fetch_opportunities() always returns [] with a warning log so callers
    see the stub in health checks / metrics without ingesting stale data.
    """

    # Marks this as a stub so get_active_adapters() can filter it out.
    is_stub: bool = True

    async def fetch_opportunities(
        self,
        symbols: list[str] | None = None,
        chains: list[Chain] | None = None,
    ) -> list[MarketOpportunity]:
        log.warning(
            "stub_adapter_called",
            adapter=self.protocol_slug,
            message="Stub adapter — not yet implemented",
        )
        return []

    async def health_check(self) -> dict[str, Any]:
        return {
            "status": "degraded",
            "last_success": None,
            "error": "Stub adapter — not yet implemented",
        }

    @property
    def requires_api_key(self) -> bool:
        return False

    @property
    def api_key_env_var(self) -> str | None:
        return None


# ---------------------------------------------------------------------------
# Venus (BSC)
# ---------------------------------------------------------------------------


class VenusAdapter(StubAdapter):
    """
    Venus Protocol — BSC lending (Compound V2 fork with tokenomics).

    TODO: Implement using Venus public REST API or subgraph:
      - Official API: https://api.venus.io/api/governance/venus
      - Subgraph: https://api.thegraph.com/subgraphs/name/venusprotocol/venus-subgraph
      - Key endpoint: /api/markets — returns all vToken markets with supply/borrow APY,
        TVL, collateral factor, borrow cap, supply cap.
      - APY encoding: Venus API returns rates as decimal fractions per block;
        convert using: apy = (1 + rate_per_block * blocks_per_year)^1 - 1
        OR use the pre-computed annualizedSupplyApy / annualizedBorrowApy fields.
      - Receipt tokens: vBNB, vUSDT, vBTC, etc. (vToken naming convention)
      - XVS governance token rewards are additional on top of base APY.
      - Key assets: BNB, USDT, BUSD, USDC, BTC (BTCB), ETH, XVS, VAI stablecoin
    """

    @property
    def venue(self) -> Venue:
        return Venue.VENUS

    @property
    def protocol_name(self) -> str:
        return "Venus"

    @property
    def protocol_slug(self) -> str:
        return "venus"

    @property
    def supported_chains(self) -> list[Chain]:
        return [Chain.BSC]

    @property
    def refresh_interval_seconds(self) -> int:
        return 600


# ---------------------------------------------------------------------------
# Radiant Capital (multi-chain)
# ---------------------------------------------------------------------------


class RadiantAdapter(StubAdapter):
    """
    Radiant Capital — cross-chain lending (Aave V2 fork with RDNT tokenomics).

    TODO: Implement using the Radiant subgraph or DeFiLlama:
      - DeFiLlama: project="radiant-v2", chains: Arbitrum, BSC, Ethereum, Base
      - Subgraph (Arbitrum): https://api.thegraph.com/subgraphs/name/radiantcapital/radiant
      - The Messari-compatible Aave V2 subgraph schema should work here.
      - Key fields: same as SparkLend (lending reserves, rates, LTV, collateral).
      - RDNT token rewards are a significant portion of the effective APY.
      - APY encoding: Messari subgraph returns percentages (5.0 = 5%) — pass-through.
      - Key assets: WBTC, WETH, USDC, USDT, wstETH, ARB on Arbitrum;
        BUSD, BNB, USDT, ETH on BSC.
    """

    @property
    def venue(self) -> Venue:
        return Venue.RADIANT

    @property
    def protocol_name(self) -> str:
        return "Radiant Capital"

    @property
    def protocol_slug(self) -> str:
        return "radiant"

    @property
    def supported_chains(self) -> list[Chain]:
        return [Chain.ARBITRUM, Chain.BSC, Chain.ETHEREUM, Chain.BASE]

    @property
    def refresh_interval_seconds(self) -> int:
        return 600


# ---------------------------------------------------------------------------
# Fluid / Instadapp (Ethereum)
# ---------------------------------------------------------------------------


class FluidAdapter(StubAdapter):
    """
    Fluid (formerly Instadapp Lite) — Ethereum lending with smart collateral.

    TODO: Implement using Fluid's public REST API or DeFiLlama:
      - DeFiLlama: project="fluid" on Ethereum and Arbitrum chains.
      - Official API: https://api.fluid.instadapp.io (check for public endpoints)
      - Fluid uses a novel "smart collateral" model: collateral earns yield while
        being used as borrow collateral, creating a net lower effective borrow cost.
      - Key products: iUSDC vault, iETH vault — both supply-only from user perspective.
      - Receipt tokens: iTokens (iUSDC, iETH) — interest-bearing ERC-20s.
      - APY encoding: DeFiLlama apy field is already a percentage.
      - Key assets: USDC, ETH/WETH, USDT, wstETH on Ethereum and Arbitrum.
    """

    @property
    def venue(self) -> Venue:
        return Venue.FLUID

    @property
    def protocol_name(self) -> str:
        return "Fluid"

    @property
    def protocol_slug(self) -> str:
        return "fluid"

    @property
    def supported_chains(self) -> list[Chain]:
        return [Chain.ETHEREUM, Chain.ARBITRUM]

    @property
    def refresh_interval_seconds(self) -> int:
        return 600


# ---------------------------------------------------------------------------
# Benqi (Avalanche)
# ---------------------------------------------------------------------------


class BenqiAdapter(StubAdapter):
    """
    Benqi — Avalanche lending + liquid staking (Compound V2 fork).

    TODO: Implement using Benqi's REST API or DeFiLlama:
      - DeFiLlama: project="benqi-lending" for lending markets,
                   project="benqi" for sAVAX liquid staking.
      - Official API: https://api.benqi.fi (check for public market endpoints)
      - Subgraph: https://api.thegraph.com/subgraphs/name/benqi-fi/benqi
      - APY encoding: check API — likely Compound-style per-block rates or pre-annualised %.
      - QI governance token rewards are distributed on top of base APY.
      - sAVAX is Benqi's liquid staking token for Avalanche validators.
      - Receipt tokens: qiUSDC, qiUSDT, qiAVAX, qiWETH, etc.
      - Key assets: AVAX, USDC, USDT, WBTC.e, WETH.e, sAVAX, QI.
    """

    @property
    def venue(self) -> Venue:
        return Venue.BENQI

    @property
    def protocol_name(self) -> str:
        return "Benqi"

    @property
    def protocol_slug(self) -> str:
        return "benqi"

    @property
    def supported_chains(self) -> list[Chain]:
        return [Chain.AVALANCHE]

    @property
    def refresh_interval_seconds(self) -> int:
        return 600


# ---------------------------------------------------------------------------
# Silo Finance (Ethereum + Arbitrum)
# ---------------------------------------------------------------------------


class SiloAdapter(StubAdapter):
    """
    Silo Finance — isolated lending silos (each silo is an independent market).

    TODO: Implement using Silo's subgraph or DeFiLlama:
      - DeFiLlama: project="silo-finance" on Ethereum and Arbitrum.
      - Silo V2 subgraph (Arbitrum): see https://docs.silo.finance/developers/subgraph
      - Key distinction: every silo pair is isolated — e.g. "ETH/USDC silo" means
        ETH and USDC are only cross-collateralised within that silo.
      - Each silo produces two assets: bridgeAsset (USDC/XAI) + non-bridge asset.
      - APY: subgraph rate fields are per-second or per-block; convert to annual.
        DeFiLlama pre-annualises for convenience.
      - XAI is Silo's native stablecoin used as a bridge asset in many silos.
      - Receipt tokens: siloXxx tokens (non-transferable in V1, transferable in V2).
      - Key assets: WETH, wstETH, ARB, GMX, GLP, USDC, USDT, XAI on Arbitrum;
        WETH, wstETH, LINK, CRV on Ethereum.
    """

    @property
    def venue(self) -> Venue:
        return Venue.SILO

    @property
    def protocol_name(self) -> str:
        return "Silo Finance"

    @property
    def protocol_slug(self) -> str:
        return "silo"

    @property
    def supported_chains(self) -> list[Chain]:
        return [Chain.ETHEREUM, Chain.ARBITRUM]

    @property
    def refresh_interval_seconds(self) -> int:
        return 600
