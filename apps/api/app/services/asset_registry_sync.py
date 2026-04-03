"""
Startup sync: pushes the in-memory asset-registry package into the Postgres
ontology tables using upsert so restarts are idempotent.

Tables written:
  asset_definitions       ← ASSET_REGISTRY
  asset_chain_deployments ← AssetDefinition.native_chains + decimals_by_chain
  venue_symbol_mappings   ← VENUE_MAPPINGS
  conversion_edges        ← CONVERSION_GRAPH
"""
from __future__ import annotations

import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from asset_registry import (
    ASSET_REGISTRY,
    CONVERSION_GRAPH,
    VENUE_MAPPINGS,
)

log = structlog.get_logger(__name__)


async def sync_asset_registry(db: AsyncSession) -> dict[str, int]:
    """
    Upsert all registry data into the four ontology tables.
    Returns a dict of {table: rows_affected}.
    """
    counts: dict[str, int] = {}

    counts["asset_definitions"] = await _sync_asset_definitions(db)
    counts["asset_chain_deployments"] = await _sync_chain_deployments(db)
    counts["venue_symbol_mappings"] = await _sync_venue_mappings(db)
    counts["conversion_edges"] = await _sync_conversion_edges(db)

    await db.commit()
    log.info("asset_registry_synced", counts=counts)
    return counts


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


async def _sync_asset_definitions(db: AsyncSession) -> int:
    from app.models.asset import AssetDefinitionRow  # local to avoid circular import

    rows = [
        {
            "canonical_id": asset.canonical_id,
            "name": asset.name,
            "umbrella": asset.umbrella.value,
            "sub_type": asset.sub_type.value,
            "fungibility": asset.fungibility.value,
            "coingecko_id": asset.coingecko_id,
            "underlying_asset_id": asset.underlying_asset_id,
            "tags": list(asset.tags),
        }
        for asset in ASSET_REGISTRY.values()
    ]

    stmt = pg_insert(AssetDefinitionRow).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["canonical_id"],
        set_={
            "name": stmt.excluded.name,
            "umbrella": stmt.excluded.umbrella,
            "sub_type": stmt.excluded.sub_type,
            "fungibility": stmt.excluded.fungibility,
            "coingecko_id": stmt.excluded.coingecko_id,
            "underlying_asset_id": stmt.excluded.underlying_asset_id,
            "tags": stmt.excluded.tags,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    await db.execute(stmt)
    return len(rows)


async def _sync_chain_deployments(db: AsyncSession) -> int:
    from app.models.asset import AssetChainDeployment  # local to avoid circular import

    rows = []
    for asset in ASSET_REGISTRY.values():
        # Build the union of all chains this asset appears on
        all_chains = set(asset.native_chains)
        all_chains.update(asset.decimals_by_chain.keys())

        for chain in all_chains:
            rows.append(
                {
                    "canonical_id": asset.canonical_id,
                    "chain": chain.value,
                    "contract_address": None,
                    "decimals": asset.decimals_by_chain.get(chain, 18),
                    "is_native": chain in asset.native_chains,
                }
            )

    if not rows:
        return 0

    stmt = pg_insert(AssetChainDeployment).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_asset_chain",
        set_={
            "decimals": stmt.excluded.decimals,
            "is_native": stmt.excluded.is_native,
        },
    )
    await db.execute(stmt)
    return len(rows)


async def _sync_venue_mappings(db: AsyncSession) -> int:
    from app.models.asset import VenueSymbolMapping  # local to avoid circular import

    rows = [
        {
            "venue": mapping.venue.value,
            "venue_symbol": mapping.venue_symbol,
            "canonical_id": mapping.canonical_id,
            # Store '' instead of NULL so unique constraint works correctly
            "chain": mapping.chain.value if mapping.chain is not None else "",
            "is_contract_address": mapping.is_contract_address,
            "notes": mapping.notes,
        }
        for mapping in VENUE_MAPPINGS
    ]

    if not rows:
        return 0

    stmt = pg_insert(VenueSymbolMapping).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_venue_symbol_chain",
        set_={
            "canonical_id": stmt.excluded.canonical_id,
            "is_contract_address": stmt.excluded.is_contract_address,
            "notes": stmt.excluded.notes,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    await db.execute(stmt)
    return len(rows)


async def _sync_conversion_edges(db: AsyncSession) -> int:
    from app.models.asset import ConversionEdgeRow  # local to avoid circular import

    rows = [
        {
            "from_asset": edge.from_asset,
            "to_asset": edge.to_asset,
            "method": edge.method.value,
            "chain": edge.chain.value,
            "protocol": edge.protocol,
            "estimated_gas_usd": edge.estimated_gas_usd,
            "fee_bps": edge.fee_bps,
            "slippage_bps_estimate": edge.slippage_bps_estimate,
            "min_duration_seconds": edge.min_duration_seconds,
            "max_duration_seconds": edge.max_duration_seconds,
            "is_deterministic": edge.is_deterministic,
            "capacity_limited": edge.capacity_limited,
            "notes": edge.notes,
        }
        for edge in CONVERSION_GRAPH
    ]

    if not rows:
        return 0

    stmt = pg_insert(ConversionEdgeRow).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_conversion_edge",
        set_={
            "protocol": stmt.excluded.protocol,
            "estimated_gas_usd": stmt.excluded.estimated_gas_usd,
            "fee_bps": stmt.excluded.fee_bps,
            "slippage_bps_estimate": stmt.excluded.slippage_bps_estimate,
            "min_duration_seconds": stmt.excluded.min_duration_seconds,
            "max_duration_seconds": stmt.excluded.max_duration_seconds,
            "is_deterministic": stmt.excluded.is_deterministic,
            "capacity_limited": stmt.excluded.capacity_limited,
            "notes": stmt.excluded.notes,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    await db.execute(stmt)
    return len(rows)
