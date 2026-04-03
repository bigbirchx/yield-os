"""
Connector package — central import point for all protocol adapters.

Usage::

    from app.connectors import get_all_adapters, get_active_adapters

    for adapter in get_active_adapters():
        opps = await adapter.safe_fetch_opportunities()
"""
from __future__ import annotations

from app.connectors.aave_v3 import AaveV3Adapter
from app.connectors.base_adapter import AdapterRegistry, ProtocolAdapter
from app.connectors.compound_v3 import CompoundV3Adapter
from app.connectors.euler_v2 import EulerV2Adapter
from app.connectors.jupiter import JupiterAdapter
from app.connectors.justlend import JustLendAdapter
from app.connectors.kamino import KaminoAdapter
from app.connectors.katana import KatanaAdapter
from app.connectors.morpho import MorphoAdapter
from app.connectors.pendle import PendleAdapter
from app.connectors.spark import SparkAdapter
from app.connectors.stubs import (
    BenqiAdapter,
    FluidAdapter,
    RadiantAdapter,
    SiloAdapter,
    StubAdapter,
    VenusAdapter,
)

__all__ = [
    # Active adapters
    "AaveV3Adapter",
    "CompoundV3Adapter",
    "EulerV2Adapter",
    "JupiterAdapter",
    "JustLendAdapter",
    "KaminoAdapter",
    "KatanaAdapter",
    "MorphoAdapter",
    "PendleAdapter",
    "SparkAdapter",
    # Stub adapters
    "BenqiAdapter",
    "FluidAdapter",
    "RadiantAdapter",
    "SiloAdapter",
    "VenusAdapter",
    # Base classes
    "AdapterRegistry",
    "ProtocolAdapter",
    "StubAdapter",
    # Factory functions
    "get_all_adapters",
    "get_active_adapters",
]


def get_all_adapters() -> list[ProtocolAdapter]:
    """Instantiate and return every known adapter, including stubs."""
    return [
        # Active / fully-implemented adapters
        AaveV3Adapter(),
        MorphoAdapter(),
        CompoundV3Adapter(),
        EulerV2Adapter(),
        SparkAdapter(),
        KaminoAdapter(),
        JupiterAdapter(),
        PendleAdapter(),
        JustLendAdapter(),
        KatanaAdapter(),
        # Stub adapters (return empty lists until implemented)
        VenusAdapter(),
        RadiantAdapter(),
        FluidAdapter(),
        BenqiAdapter(),
        SiloAdapter(),
    ]


def get_active_adapters() -> list[ProtocolAdapter]:
    """Return only fully-implemented (non-stub) adapters."""
    return [a for a in get_all_adapters() if not getattr(a, "is_stub", False)]
