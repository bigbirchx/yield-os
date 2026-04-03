"""
Unified opportunity ingestion orchestrator.

Calls every registered :class:`ProtocolAdapter`, upserts results into the
``market_opportunities`` table, and records time-series snapshots in
``market_opportunity_snapshots``.

Usage::

    from app.services.opportunity_ingestion import OpportunityIngestionService

    svc = OpportunityIngestionService()
    result = await svc.run_full_ingestion(db)
"""
from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base_adapter import AdapterRegistry, ProtocolAdapter
from app.models.opportunity import (
    MarketOpportunityRow,
    MarketOpportunitySnapshotRow,
)
from asset_registry import Venue
from opportunity_schema import MarketOpportunity

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level adapter registry (populated at startup)
# ---------------------------------------------------------------------------

_registry = AdapterRegistry()


def get_registry() -> AdapterRegistry:
    """Return the global adapter registry."""
    return _registry


def register_adapters() -> None:
    """Register all available adapters.  Called once at startup."""
    from app.connectors import get_all_adapters

    for adapter in get_all_adapters():
        _registry.register(adapter)

    active = sum(1 for a in _registry.get_all() if not getattr(a, "is_stub", False))
    stubs = len(_registry.get_all()) - active
    log.info(
        "opportunity_adapters_registered",
        total=len(_registry.get_all()),
        active=active,
        stubs=stubs,
    )


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _opportunity_to_row(opp: MarketOpportunity) -> dict[str, Any]:
    """Convert a Pydantic MarketOpportunity to a flat dict for DB upsert.

    Uses ``model_dump()`` (Python mode) so datetime fields remain as real
    ``datetime`` objects — asyncpg requires native types, not ISO strings.
    JSONB columns (reward_breakdown, liquidity, etc.) need plain dicts, so
    we use ``model_dump(mode="json")`` selectively for those sub-models.
    """
    # Python-mode dump keeps datetimes as datetime objects (asyncpg needs this)
    d = opp.model_dump()
    # JSON-mode dump for JSONB columns — converts sub-models to plain dicts
    d_json = opp.model_dump(mode="json")
    # Enum values need .value extraction in Python mode
    side_val = d["side"].value if hasattr(d["side"], "value") else d["side"]
    opp_type_val = d["opportunity_type"].value if hasattr(d["opportunity_type"], "value") else d["opportunity_type"]
    dur_val = d["effective_duration"].value if hasattr(d["effective_duration"], "value") else d["effective_duration"]
    return {
        "opportunity_id": d["opportunity_id"],
        "venue": d["venue"],
        "chain": d["chain"],
        "protocol": d["protocol"],
        "protocol_slug": d["protocol_slug"],
        "market_id": d["market_id"],
        "market_name": d.get("market_name"),
        "side": side_val,
        "asset_id": d["asset_id"],
        "asset_symbol": d["asset_symbol"],
        "umbrella_group": d["umbrella_group"],
        "asset_sub_type": d["asset_sub_type"],
        "opportunity_type": opp_type_val,
        "effective_duration": dur_val,
        "maturity_date": d.get("maturity_date"),
        "days_to_maturity": d.get("days_to_maturity"),
        "total_apy_pct": d["total_apy_pct"],
        "base_apy_pct": d["base_apy_pct"],
        "reward_breakdown": d_json.get("reward_breakdown", []),
        "total_supplied": d.get("total_supplied"),
        "total_supplied_usd": d.get("total_supplied_usd"),
        "total_borrowed": d.get("total_borrowed"),
        "total_borrowed_usd": d.get("total_borrowed_usd"),
        "capacity_cap": d.get("capacity_cap"),
        "capacity_remaining": d.get("capacity_remaining"),
        "is_capacity_capped": d.get("is_capacity_capped", False),
        "tvl_usd": d.get("tvl_usd"),
        "liquidity": d_json.get("liquidity", {}),
        "rate_model": d_json.get("rate_model"),
        "is_collateral_eligible": d.get("is_collateral_eligible", False),
        "as_collateral_max_ltv_pct": d.get("as_collateral_max_ltv_pct"),
        "as_collateral_liquidation_ltv_pct": d.get("as_collateral_liquidation_ltv_pct"),
        "collateral_options": d_json.get("collateral_options"),
        "receipt_token": d_json.get("receipt_token"),
        "is_amm_lp": d.get("is_amm_lp", False),
        "is_pendle": d.get("is_pendle", False),
        "pendle_type": d.get("pendle_type"),
        "tags": d_json.get("tags", []),
        "data_source": d["data_source"],
        "last_updated_at": d["last_updated_at"],
        "data_freshness_seconds": d.get("data_freshness_seconds", 0),
        "source_url": d.get("source_url"),
    }


def _opportunity_to_snapshot(opp: MarketOpportunity, now: datetime) -> dict[str, Any]:
    """Build a snapshot row dict from a MarketOpportunity."""
    util_pct = None
    if opp.liquidity and opp.liquidity.utilization_rate_pct is not None:
        util_pct = opp.liquidity.utilization_rate_pct
    return {
        "opportunity_id": opp.opportunity_id,
        "snapshot_at": now,
        "total_apy_pct": opp.total_apy_pct,
        "base_apy_pct": opp.base_apy_pct,
        "total_supplied": opp.total_supplied,
        "total_supplied_usd": opp.total_supplied_usd,
        "total_borrowed": opp.total_borrowed,
        "total_borrowed_usd": opp.total_borrowed_usd,
        "utilization_rate_pct": util_pct,
        "tvl_usd": opp.tvl_usd,
    }


# ---------------------------------------------------------------------------
# Upsert helpers
# ---------------------------------------------------------------------------

# Columns to update on conflict (everything except PK and created_at)
_UPSERT_COLUMNS = [
    "venue", "chain", "protocol", "protocol_slug", "market_id", "market_name",
    "side", "asset_id", "asset_symbol", "umbrella_group", "asset_sub_type",
    "opportunity_type", "effective_duration", "maturity_date", "days_to_maturity",
    "total_apy_pct", "base_apy_pct", "reward_breakdown",
    "total_supplied", "total_supplied_usd", "total_borrowed", "total_borrowed_usd",
    "capacity_cap", "capacity_remaining", "is_capacity_capped", "tvl_usd",
    "liquidity", "rate_model",
    "is_collateral_eligible", "as_collateral_max_ltv_pct", "as_collateral_liquidation_ltv_pct",
    "collateral_options", "receipt_token",
    "is_amm_lp", "is_pendle", "pendle_type", "tags",
    "data_source", "last_updated_at", "data_freshness_seconds", "source_url",
]


async def _upsert_opportunities(
    db: AsyncSession,
    opportunities: list[MarketOpportunity],
) -> int:
    """Upsert a batch of opportunities into market_opportunities."""
    if not opportunities:
        return 0

    rows = [_opportunity_to_row(opp) for opp in opportunities]

    # Batch in chunks of 500 to avoid overly large statements
    upserted = 0
    for i in range(0, len(rows), 500):
        chunk = rows[i : i + 500]
        stmt = pg_insert(MarketOpportunityRow).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=["opportunity_id"],
            set_={
                col: stmt.excluded[col]
                for col in _UPSERT_COLUMNS
            } | {"updated_at": func.now()},
        )
        await db.execute(stmt)
        upserted += len(chunk)

    return upserted


async def _insert_snapshots(
    db: AsyncSession,
    opportunities: list[MarketOpportunity],
    now: datetime,
) -> int:
    """Insert snapshot rows for rate history."""
    if not opportunities:
        return 0

    rows = [_opportunity_to_snapshot(opp, now) for opp in opportunities]

    for i in range(0, len(rows), 500):
        chunk = rows[i : i + 500]
        stmt = pg_insert(MarketOpportunitySnapshotRow).values(chunk)
        await db.execute(stmt)

    return len(rows)


# ---------------------------------------------------------------------------
# Ingestion service
# ---------------------------------------------------------------------------


class OpportunityIngestionService:
    """Orchestrates adapter calls and stores results."""

    def __init__(self, registry: AdapterRegistry | None = None) -> None:
        self._registry = registry or _registry

    async def run_full_ingestion(
        self,
        db: AsyncSession,
        *,
        take_snapshots: bool = True,
    ) -> dict[str, Any]:
        """Call all registered adapters in parallel and upsert results.

        Returns a summary dict with counts and timing.
        """
        adapters = self._registry.get_all()
        if not adapters:
            log.warning("opportunity_ingestion_no_adapters")
            return {"total_opportunities": 0, "by_venue": {}, "errors": [], "duration_seconds": 0}

        t0 = time.monotonic()
        now = datetime.now(UTC)
        errors: list[str] = []
        by_venue: dict[str, int] = {}
        all_opportunities: list[MarketOpportunity] = []

        # Fetch from all adapters in parallel
        tasks = {
            adapter.venue.value: adapter.safe_fetch_opportunities()
            for adapter in adapters
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        for venue_key, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                errors.append(f"{venue_key}: {result}")
                by_venue[venue_key] = 0
                log.error("opportunity_ingestion_adapter_error", venue=venue_key, error=str(result))
            else:
                all_opportunities.extend(result)
                by_venue[venue_key] = len(result)

        # Upsert to database
        upserted = await _upsert_opportunities(db, all_opportunities)

        # Take snapshots if requested
        snapshot_count = 0
        if take_snapshots and all_opportunities:
            snapshot_count = await _insert_snapshots(db, all_opportunities, now)

        await db.commit()

        duration = time.monotonic() - t0
        log.info(
            "opportunity_ingestion_complete",
            total=upserted,
            snapshots=snapshot_count,
            by_venue=by_venue,
            errors=len(errors),
            duration_s=round(duration, 2),
        )
        return {
            "total_opportunities": upserted,
            "by_venue": by_venue,
            "errors": errors,
            "duration_seconds": round(duration, 2),
        }

    async def run_adapter(
        self,
        db: AsyncSession,
        venue: Venue,
        *,
        take_snapshots: bool = True,
    ) -> dict[str, Any]:
        """Run a single adapter and upsert its results."""
        adapter = self._registry.get_by_venue(venue)
        if adapter is None:
            return {"error": f"No adapter registered for {venue.value}", "total_opportunities": 0}

        t0 = time.monotonic()
        now = datetime.now(UTC)

        opportunities = await adapter.safe_fetch_opportunities()
        upserted = await _upsert_opportunities(db, opportunities)

        snapshot_count = 0
        if take_snapshots and opportunities:
            snapshot_count = await _insert_snapshots(db, opportunities, now)

        await db.commit()

        duration = time.monotonic() - t0
        log.info(
            "opportunity_adapter_run",
            venue=venue.value,
            total=upserted,
            snapshots=snapshot_count,
            duration_s=round(duration, 2),
        )
        return {
            "venue": venue.value,
            "total_opportunities": upserted,
            "snapshots": snapshot_count,
            "duration_seconds": round(duration, 2),
        }

    async def run_due_adapters(
        self,
        db: AsyncSession,
        *,
        take_snapshots: bool = True,
    ) -> dict[str, Any]:
        """Run only adapters that are due for a refresh."""
        due = self._registry.get_due_for_refresh()
        if not due:
            return {"total_opportunities": 0, "by_venue": {}, "errors": [], "duration_seconds": 0}

        t0 = time.monotonic()
        now = datetime.now(UTC)
        errors: list[str] = []
        by_venue: dict[str, int] = {}
        all_opportunities: list[MarketOpportunity] = []

        tasks = {
            adapter.venue.value: adapter.safe_fetch_opportunities()
            for adapter in due
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        for venue_key, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                errors.append(f"{venue_key}: {result}")
                by_venue[venue_key] = 0
            else:
                all_opportunities.extend(result)
                by_venue[venue_key] = len(result)

        upserted = await _upsert_opportunities(db, all_opportunities)
        snapshot_count = 0
        if take_snapshots and all_opportunities:
            snapshot_count = await _insert_snapshots(db, all_opportunities, now)

        await db.commit()

        duration = time.monotonic() - t0
        return {
            "total_opportunities": upserted,
            "by_venue": by_venue,
            "errors": errors,
            "duration_seconds": round(duration, 2),
        }

    async def prune_stale(
        self,
        db: AsyncSession,
        max_age_hours: int = 24,
    ) -> int:
        """Delete opportunities not updated within max_age_hours."""
        cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours)
        result = await db.execute(
            delete(MarketOpportunityRow).where(
                MarketOpportunityRow.last_updated_at < cutoff,
            ),
        )
        await db.commit()
        deleted = result.rowcount
        if deleted:
            log.info("opportunity_prune_stale", deleted=deleted, cutoff=cutoff.isoformat())
        return deleted

    async def get_snapshot_stats(self, db: AsyncSession) -> dict[str, Any]:
        """Return aggregate stats about the opportunity table."""
        # Total count
        total = (await db.execute(
            select(func.count()).select_from(MarketOpportunityRow)
        )).scalar() or 0

        # By venue
        venue_rows = (await db.execute(
            select(
                MarketOpportunityRow.venue,
                func.count().label("n"),
            ).group_by(MarketOpportunityRow.venue)
        )).all()

        # By chain
        chain_rows = (await db.execute(
            select(
                MarketOpportunityRow.chain,
                func.count().label("n"),
            ).group_by(MarketOpportunityRow.chain)
        )).all()

        # By type
        type_rows = (await db.execute(
            select(
                MarketOpportunityRow.opportunity_type,
                func.count().label("n"),
            ).group_by(MarketOpportunityRow.opportunity_type)
        )).all()

        # Last update per venue
        last_update_rows = (await db.execute(
            select(
                MarketOpportunityRow.venue,
                func.max(MarketOpportunityRow.last_updated_at).label("last"),
            ).group_by(MarketOpportunityRow.venue)
        )).all()

        return {
            "total_opportunities": total,
            "by_venue": {r.venue: r.n for r in venue_rows},
            "by_chain": {r.chain: r.n for r in chain_rows},
            "by_type": {r.opportunity_type: r.n for r in type_rows},
            "last_update_by_venue": {
                r.venue: r.last.isoformat() if r.last else None
                for r in last_update_rows
            },
        }
