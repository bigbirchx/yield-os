"""
Asset registry endpoints — canonical taxonomy, normalisation, and conversion graph.

No database access; all data is served from the in-process asset-registry package.

Canonical IDs are mixed-case (e.g. "wstETH", "cbETH") — the API accepts any
casing from callers and resolves to the authoritative form via _lookup().
"""
from fastapi import APIRouter, HTTPException, Query

from asset_registry import (
    ASSET_REGISTRY,
    CONVERSION_GRAPH,
    AssetNormalizer,
    AssetUmbrella,
    Chain,
    ConversionRouter,
    Venue,
    get_fungible_group,
    get_umbrella_assets,
)

router = APIRouter(prefix="/api/assets", tags=["assets"])

_normalizer = AssetNormalizer()
_router = ConversionRouter()

# Case-insensitive resolution map: "WSTETH" → "wstETH"
_UPPER_MAP: dict[str, str] = {k.upper(): k for k in ASSET_REGISTRY}


def _lookup(raw: str) -> str | None:
    """Return the canonical ID for *raw* (case-insensitive), or None."""
    return _UPPER_MAP.get(raw.upper())


def _require_asset(raw: str) -> str:
    """Resolve *raw* to a canonical ID or raise 404."""
    canonical = _lookup(raw)
    if canonical is None:
        raise HTTPException(status_code=404, detail=f"Asset '{raw}' not found")
    return canonical


@router.get("/registry")
async def list_registry():
    """Return all assets in the canonical registry."""
    return {
        canonical_id: asset.model_dump()
        for canonical_id, asset in ASSET_REGISTRY.items()
    }


@router.get("/registry/{canonical_id}")
async def get_asset(canonical_id: str):
    """Return a single asset definition by canonical ID."""
    return ASSET_REGISTRY[_require_asset(canonical_id)].model_dump()


@router.get("/umbrella/{umbrella}")
async def list_umbrella(umbrella: str):
    """Return all assets belonging to a given umbrella (USD, ETH, BTC, SOL, HYPE, OTHER)."""
    try:
        umbrella_enum = AssetUmbrella(umbrella.upper())
    except ValueError:
        valid = [u.value for u in AssetUmbrella]
        raise HTTPException(
            status_code=422,
            detail=f"Invalid umbrella '{umbrella}'. Valid values: {valid}",
        )
    assets = get_umbrella_assets(umbrella_enum)
    return [a.model_dump() for a in assets]


@router.get("/normalize")
async def normalize_symbol(
    venue: str = Query(..., description="Venue identifier (e.g. BINANCE, DEFILLAMA)"),
    symbol: str = Query(..., description="Raw venue symbol to normalise"),
    chain: str | None = Query(None, description="Optional chain context (e.g. ETHEREUM)"),
):
    """Normalise a venue-specific symbol to its canonical asset ID."""
    try:
        venue_enum = Venue(venue.upper())
    except ValueError:
        valid = [v.value for v in Venue]
        raise HTTPException(
            status_code=422,
            detail=f"Unknown venue '{venue}'. Valid values: {valid}",
        )

    chain_enum: Chain | None = None
    if chain:
        try:
            chain_enum = Chain(chain.upper())
        except ValueError:
            valid_chains = [c.value for c in Chain]
            raise HTTPException(
                status_code=422,
                detail=f"Unknown chain '{chain}'. Valid values: {valid_chains}",
            )

    canonical_id = _normalizer.normalize(venue_enum, symbol, chain=chain_enum)
    return {
        "venue": venue_enum.value,
        "raw_symbol": symbol,
        "chain": chain_enum.value if chain_enum else None,
        "canonical_id": canonical_id,
        "resolved": canonical_id is not None,
    }


@router.get("/conversions")
async def find_conversions(
    from_id: str = Query(..., alias="from", description="Source canonical asset ID"),
    to_id: str = Query(..., alias="to", description="Target canonical asset ID"),
    amount_usd: float = Query(1_000_000.0, description="Trade size in USD for cost estimation"),
    chain: str | None = Query(None, description="Optional chain filter"),
):
    """Find conversion paths between two assets and estimate costs."""
    from_canonical = _require_asset(from_id)
    to_canonical = _require_asset(to_id)

    chain_enum: Chain | None = None
    if chain:
        try:
            chain_enum = Chain(chain.upper())
        except ValueError:
            valid_chains = [c.value for c in Chain]
            raise HTTPException(
                status_code=422,
                detail=f"Unknown chain '{chain}'. Valid values: {valid_chains}",
            )

    paths = _router.find_conversion_path(from_canonical, to_canonical, chain=chain_enum)

    result = []
    for path in paths:
        cost = _router.estimate_conversion_cost(path, amount_usd)
        result.append(
            {
                "path": [edge.model_dump() for edge in path],
                "hops": len(path),
                "cost": cost,
            }
        )

    # Sort by total cost (fee_bps + slippage_bps ascending)
    result.sort(key=lambda x: x["cost"]["total_fee_bps"] + x["cost"]["total_slippage_bps"])

    return {
        "from": from_canonical,
        "to": to_canonical,
        "amount_usd": amount_usd,
        "chain_filter": chain_enum.value if chain_enum else None,
        "paths_found": len(result),
        "paths": result,
    }


@router.get("/fungible-group/{canonical_id}")
async def fungible_group(canonical_id: str):
    """Return all assets in the same fungibility group as the given asset."""
    resolved = _require_asset(canonical_id)
    group = get_fungible_group(resolved)
    return {
        "canonical_id": resolved,
        "fungible_group": group,
        "assets": [ASSET_REGISTRY[aid].model_dump() for aid in group if aid in ASSET_REGISTRY],
    }
