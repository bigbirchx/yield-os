"""
CreditDesk WACC Export import service.

Reads an Excel workbook with three sheets:
- Asset_Params: price data (for USD conversions and collateral valuation)
- Trades_Raw: the loan tape
- Observed_Collateral: collateral holdings

Produces BookPosition objects with classification, protocol extraction,
collateral allocation, and optional market rate matching.
"""
from __future__ import annotations

import math
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.book import (
    BookCollateralAllocationRow,
    BookObservedCollateralRow,
    BookPositionRow,
    BookRow,
)
from app.models.opportunity import MarketOpportunityRow
from portfolio import (
    AllocatedCollateral,
    BookPosition,
    BookSummary,
    ObservedCollateral,
    PositionCategory,
    allocate_collateral,
    classify_position,
    compute_summary,
    extract_protocol_info,
    infer_chain_for_asset,
    normalize_creditdesk_symbol,
)

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Excel column name mapping
# ---------------------------------------------------------------------------

# Map CreditDesk column headers to internal field names.
# Handles minor variations in spacing and casing.

_TRADES_COL_MAP = {
    "Loan Id": "loan_id",
    "Customer Id": "customer_id",
    "Counterparty Name": "counterparty_name",
    "Counterparty Legal Entity Name": "counterparty_legal_entity",
    "Direction": "direction",
    "Principal Asset": "principal_asset",
    "Current Principal Qty": "principal_qty",
    "Current Principal Usd": "principal_usd",
    "Effective Date": "effective_date",
    "Maturity Date": "maturity_date",
    "Tenor": "tenor",
    "Recall Period": "recall_period_days",
    "Collateral Assets": "collateral_assets_raw",
    "Initial Collateralization Ratio": "initial_collateralization_ratio_pct",
    "Rehypothecation": "rehypothecation_allowed",
    "Rehypothecation Allowed": "rehypothecation_allowed",
    "Collateral Substitution": "collateral_substitution_allowed",
    "Collateral Substitution Allowed": "collateral_substitution_allowed",
    "Is Collateralized": "is_collateralized",
    "Loan Type": "loan_type",
    "Current Interest Rate": "interest_rate_pct",
    "Status": "status",
    "Query Notes": "query_notes",
}

_COLLATERAL_COL_MAP = {
    "Customer Id": "customer_id",
    "Counterparty Name": "counterparty_name",
    "Collateral Relationship": "collateral_relationship",
    "Collateral Asset": "collateral_asset",
    "Units Posted": "units_posted",
    "Data Source": "data_source",
    "Tri Party": "is_tri_party",
    "Is Tri Party": "is_tri_party",
    "Custodial Venue": "custodial_venue",
}


# ---------------------------------------------------------------------------
# Helper: safe NaN handling
# ---------------------------------------------------------------------------


def _safe_float(val: Any, default: float = 0.0) -> float:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _safe_str(val: Any, default: str | None = None) -> str | None:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return default
    return str(val).strip() or default


def _safe_bool(val: Any, default: bool = False) -> bool:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return default
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    return s in ("true", "yes", "1", "y")


def _safe_date(val: Any) -> Any | None:
    """Return a date or None for NaT/NaN."""
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(val, pd.Timestamp):
        return val.date()
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, float) and math.isnan(val):
        return None
    return val


# ---------------------------------------------------------------------------
# BookImportService
# ---------------------------------------------------------------------------


class BookImportService:
    """Import and manage CreditDesk book snapshots."""

    # ── Import from file ───────────────────────────────────────────

    async def import_from_excel(
        self,
        db: AsyncSession,
        file_path: str,
        book_name: str | None = None,
    ) -> dict:
        """Read a CreditDesk WACC Export and persist to database.

        Returns an import summary dict with book_id, position counts, etc.
        """
        book_id = str(uuid.uuid4())[:12]
        now = datetime.now(UTC)

        # ── 1. Read Asset_Params → price lookup ──────────────────
        try:
            df_prices = pd.read_excel(file_path, sheet_name="Asset_Params")
            asset_prices = self._parse_prices(df_prices)
            log.info("book_import_prices", count=len(asset_prices))
        except Exception as exc:
            log.warning("book_import_prices_error", error=str(exc))
            asset_prices = {}

        # Determine as_of date from price timestamps if available
        as_of_date: datetime | None = None
        if "Timestamp" in (df_prices.columns if "df_prices" in dir() else []):
            ts_col = df_prices["Timestamp"].dropna()
            if len(ts_col) > 0:
                as_of_date = pd.Timestamp(ts_col.iloc[0]).to_pydatetime()

        # ── 2. Read Trades_Raw → BookPositions ───────────────────
        df_trades = pd.read_excel(file_path, sheet_name="Trades_Raw")
        positions = self._parse_trades(df_trades, asset_prices)
        log.info("book_import_trades", count=len(positions))

        # ── 3. Read Observed_Collateral ──────────────────────────
        try:
            df_collateral = pd.read_excel(
                file_path, sheet_name="Observed_Collateral"
            )
            collateral = self._parse_collateral(df_collateral, now)
            log.info("book_import_collateral", count=len(collateral))
        except Exception as exc:
            log.warning("book_import_collateral_error", error=str(exc))
            collateral = []

        # ── 4. Pro-rata collateral allocation ────────────────────
        allocations = self._run_collateral_allocation(
            positions, collateral, asset_prices,
        )
        for pos in positions:
            if pos.loan_id in allocations:
                pos.allocated_collateral = allocations[pos.loan_id]

        # ── 5. Match DeFi positions to market opportunities ──────
        await self._match_defi_to_market(db, positions)

        # ── 6. Compute summary ───────────────────────────────────
        summary = compute_summary(positions)

        # ── 7. Persist ───────────────────────────────────────────
        await self._persist(
            db, book_id, book_name or file_path, file_path,
            now, as_of_date, positions, collateral, allocations, summary,
        )

        # Build classification breakdown
        cat_counts: dict[str, int] = defaultdict(int)
        for p in positions:
            cat_counts[p.category.value] += 1

        return {
            "book_id": book_id,
            "total_positions": len(positions),
            "total_collateral_observations": len(collateral),
            "total_allocations": sum(len(v) for v in allocations.values()),
            "category_breakdown": dict(cat_counts),
            "total_loan_out_usd": summary.total_loan_out_usd,
            "total_borrow_in_usd": summary.total_borrow_in_usd,
            "net_book_usd": summary.net_book_usd,
            "weighted_avg_lending_rate_pct": summary.weighted_avg_lending_rate_pct,
            "weighted_avg_borrowing_rate_pct": summary.weighted_avg_borrowing_rate_pct,
            "net_interest_margin_pct": summary.net_interest_margin_pct,
        }

    async def import_from_upload(
        self,
        db: AsyncSession,
        file_bytes: bytes,
        filename: str,
    ) -> dict:
        """Save uploaded bytes to a temp file and import."""
        import tempfile
        import os

        suffix = os.path.splitext(filename)[1] or ".xlsx"
        with tempfile.NamedTemporaryFile(
            suffix=suffix, delete=False
        ) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        try:
            return await self.import_from_excel(db, tmp_path, book_name=filename)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # ── Query methods ──────────────────────────────────────────────

    async def get_book(self, db: AsyncSession, book_id: str) -> dict | None:
        """Return full book metadata with summary."""
        row = (await db.execute(
            select(BookRow).where(BookRow.book_id == book_id)
        )).scalar_one_or_none()
        if row is None:
            return None
        return {
            "book_id": row.book_id,
            "name": row.name,
            "source_file": row.source_file,
            "import_date": row.import_date.isoformat(),
            "as_of_date": row.as_of_date.isoformat() if row.as_of_date else None,
            "total_positions": row.total_positions,
            "summary": row.summary,
        }

    async def get_positions(
        self,
        db: AsyncSession,
        book_id: str,
        *,
        category: str | None = None,
        asset: str | None = None,
        counterparty: str | None = None,
        min_rate: float | None = None,
    ) -> list[dict]:
        """Return positions with optional filtering."""
        q = select(BookPositionRow).where(BookPositionRow.book_id == book_id)
        if category:
            q = q.where(BookPositionRow.category == category.upper())
        if asset:
            q = q.where(BookPositionRow.principal_asset == asset)
        if counterparty:
            q = q.where(
                BookPositionRow.counterparty_name.ilike(f"%{counterparty}%")
            )
        if min_rate is not None:
            q = q.where(BookPositionRow.interest_rate_pct >= min_rate)

        q = q.order_by(BookPositionRow.principal_usd.desc())
        rows = (await db.execute(q)).scalars().all()
        return [self._position_row_to_dict(r) for r in rows]

    async def get_defi_positions(
        self, db: AsyncSession, book_id: str,
    ) -> list[dict]:
        """Return DeFi/staking positions with market rate comparisons."""
        q = (
            select(BookPositionRow)
            .where(BookPositionRow.book_id == book_id)
            .where(
                BookPositionRow.category.in_([
                    PositionCategory.DEFI_SUPPLY.value,
                    PositionCategory.DEFI_BORROW.value,
                    PositionCategory.NATIVE_STAKING.value,
                ])
            )
            .order_by(BookPositionRow.principal_usd.desc())
        )
        rows = (await db.execute(q)).scalars().all()
        return [self._position_row_to_dict(r) for r in rows]

    async def get_collateral(
        self, db: AsyncSession, book_id: str,
    ) -> dict:
        """Return observed collateral and allocations."""
        obs_rows = (await db.execute(
            select(BookObservedCollateralRow)
            .where(BookObservedCollateralRow.book_id == book_id)
        )).scalars().all()

        alloc_rows = (await db.execute(
            select(BookCollateralAllocationRow)
            .where(BookCollateralAllocationRow.book_id == book_id)
        )).scalars().all()

        return {
            "observed": [
                {
                    "customer_id": r.customer_id,
                    "counterparty_name": r.counterparty_name,
                    "collateral_relationship": r.collateral_relationship,
                    "collateral_asset": r.collateral_asset,
                    "units_posted": r.units_posted,
                    "data_source": r.data_source,
                    "is_tri_party": r.is_tri_party,
                    "custodial_venue": r.custodial_venue,
                }
                for r in obs_rows
            ],
            "allocations": [
                {
                    "loan_id": r.loan_id,
                    "collateral_asset": r.collateral_asset,
                    "allocated_units": r.allocated_units,
                    "allocated_usd": r.allocated_usd,
                    "allocation_weight_pct": r.allocation_weight_pct,
                }
                for r in alloc_rows
            ],
        }

    async def get_counterparty_view(
        self, db: AsyncSession, book_id: str, customer_id: int,
    ) -> dict:
        """Single counterparty: positions + collateral."""
        positions = (await db.execute(
            select(BookPositionRow)
            .where(BookPositionRow.book_id == book_id)
            .where(BookPositionRow.customer_id == customer_id)
            .order_by(BookPositionRow.principal_usd.desc())
        )).scalars().all()

        collateral = (await db.execute(
            select(BookObservedCollateralRow)
            .where(BookObservedCollateralRow.book_id == book_id)
            .where(BookObservedCollateralRow.customer_id == customer_id)
        )).scalars().all()

        alloc_loan_ids = [p.loan_id for p in positions]
        allocations = []
        if alloc_loan_ids:
            allocations = (await db.execute(
                select(BookCollateralAllocationRow)
                .where(BookCollateralAllocationRow.book_id == book_id)
                .where(BookCollateralAllocationRow.loan_id.in_(alloc_loan_ids))
            )).scalars().all()

        return {
            "customer_id": customer_id,
            "counterparty_name": positions[0].counterparty_name if positions else "",
            "positions": [self._position_row_to_dict(p) for p in positions],
            "observed_collateral": [
                {
                    "collateral_relationship": c.collateral_relationship,
                    "collateral_asset": c.collateral_asset,
                    "units_posted": c.units_posted,
                    "custodial_venue": c.custodial_venue,
                }
                for c in collateral
            ],
            "allocations": [
                {
                    "loan_id": a.loan_id,
                    "collateral_asset": a.collateral_asset,
                    "allocated_units": a.allocated_units,
                    "allocated_usd": a.allocated_usd,
                    "allocation_weight_pct": a.allocation_weight_pct,
                }
                for a in allocations
            ],
        }

    async def get_summary(self, db: AsyncSession, book_id: str) -> dict | None:
        """Return stored summary for a book."""
        row = (await db.execute(
            select(BookRow).where(BookRow.book_id == book_id)
        )).scalar_one_or_none()
        if row is None:
            return None
        return row.summary

    async def refresh_market_matching(
        self, db: AsyncSession, book_id: str,
    ) -> dict:
        """Re-match DeFi positions to current market opportunities."""
        positions = (await db.execute(
            select(BookPositionRow)
            .where(BookPositionRow.book_id == book_id)
            .where(
                BookPositionRow.category.in_([
                    PositionCategory.DEFI_SUPPLY.value,
                    PositionCategory.DEFI_BORROW.value,
                ])
            )
        )).scalars().all()

        updated = 0
        for pos in positions:
            market_rate = await self._find_market_rate(
                db,
                protocol_name=pos.protocol_name,
                chain=pos.protocol_chain,
                asset_id=pos.principal_asset,
                side="SUPPLY" if pos.category == PositionCategory.DEFI_SUPPLY.value else "BORROW",
            )
            if market_rate is not None:
                pos.current_market_rate_pct = market_rate
                pos.rate_vs_market_bps = round(
                    (pos.interest_rate_pct - market_rate) * 100.0, 2,
                )
                updated += 1

        await db.commit()
        return {"book_id": book_id, "positions_matched": updated}

    # ── Internal parse methods ────────────────────────────────────

    def _parse_prices(self, df: pd.DataFrame) -> dict[str, float]:
        """Parse Asset_Params sheet into {canonical_id: price_usd}."""
        prices: dict[str, float] = {}
        for _, row in df.iterrows():
            raw_symbol = _safe_str(row.get("Asset") or row.get("Symbol"), "")
            if not raw_symbol:
                continue
            price = _safe_float(
                row.get("Price Usd") or row.get("Price USD") or row.get("Price") or row.get("price_usd"),
            )
            if price > 0:
                canon = normalize_creditdesk_symbol(raw_symbol)
                prices[canon] = price
        return prices

    def _parse_trades(
        self,
        df: pd.DataFrame,
        asset_prices: dict[str, float],
    ) -> list[BookPosition]:
        """Parse Trades_Raw sheet into BookPosition objects."""
        # Rename columns using mapping
        rename = {}
        for col in df.columns:
            clean = col.strip()
            if clean in _TRADES_COL_MAP:
                rename[col] = _TRADES_COL_MAP[clean]
        df = df.rename(columns=rename)

        positions: list[BookPosition] = []

        for _, row in df.iterrows():
            try:
                direction = _safe_str(row.get("direction"), "")
                if direction not in ("Loan_Out", "Borrow_In"):
                    continue

                loan_type = _safe_str(row.get("loan_type"), "lending") or "lending"
                counterparty_name = _safe_str(row.get("counterparty_name"), "") or ""
                raw_asset = _safe_str(row.get("principal_asset"), "") or ""
                canon_asset = normalize_creditdesk_symbol(raw_asset)

                category = classify_position(direction, loan_type, counterparty_name)

                # Extract protocol info for DeFi/staking positions
                protocol_name = None
                protocol_chain = None
                if category in (
                    PositionCategory.DEFI_SUPPLY,
                    PositionCategory.DEFI_BORROW,
                    PositionCategory.NATIVE_STAKING,
                ):
                    protocol_name, protocol_chain = extract_protocol_info(
                        counterparty_name,
                    )
                    if protocol_chain is None:
                        protocol_chain = infer_chain_for_asset(canon_asset)

                # Umbrella group from asset registry
                umbrella = None
                try:
                    from asset_registry.taxonomy import ASSET_REGISTRY

                    asset_def = ASSET_REGISTRY.get(canon_asset)
                    if asset_def:
                        umbrella = asset_def.umbrella.value
                except Exception:
                    pass

                # Interest rate: CreditDesk provides annualised percentage
                rate = _safe_float(row.get("interest_rate_pct"))

                pos = BookPosition(
                    loan_id=int(_safe_float(row.get("loan_id"))),
                    customer_id=int(_safe_float(row.get("customer_id"))),
                    counterparty_name=counterparty_name,
                    counterparty_legal_entity=_safe_str(
                        row.get("counterparty_legal_entity"),
                    ),
                    category=category,
                    direction=direction,
                    principal_asset=canon_asset,
                    principal_qty=_safe_float(row.get("principal_qty")),
                    principal_usd=_safe_float(row.get("principal_usd")),
                    effective_date=_safe_date(row.get("effective_date"))
                    or datetime.now(UTC).date(),
                    maturity_date=_safe_date(row.get("maturity_date")),
                    tenor=_safe_str(row.get("tenor"), "Open") or "Open",
                    recall_period_days=_safe_float(
                        row.get("recall_period_days"), 0.0,
                    ) or None,
                    collateral_assets_raw=_safe_str(
                        row.get("collateral_assets_raw"),
                    ),
                    initial_collateralization_ratio_pct=_safe_float(
                        row.get("initial_collateralization_ratio_pct"),
                    ) or None,
                    rehypothecation_allowed=_safe_bool(
                        row.get("rehypothecation_allowed"),
                    ),
                    collateral_substitution_allowed=_safe_bool(
                        row.get("collateral_substitution_allowed"),
                    ),
                    is_collateralized=_safe_bool(row.get("is_collateralized")),
                    loan_type=loan_type,
                    interest_rate_pct=rate,
                    status=_safe_str(row.get("status"), "active") or "active",
                    query_notes=_safe_str(row.get("query_notes")),
                    protocol_name=protocol_name,
                    protocol_chain=protocol_chain,
                    umbrella_group=umbrella,
                )
                positions.append(pos)
            except Exception:
                log.debug("book_import_skip_trade_row", exc_info=True)
                continue

        return positions

    def _parse_collateral(
        self,
        df: pd.DataFrame,
        asof_date: datetime,
    ) -> list[ObservedCollateral]:
        """Parse Observed_Collateral sheet."""
        rename = {}
        for col in df.columns:
            clean = col.strip()
            if clean in _COLLATERAL_COL_MAP:
                rename[col] = _COLLATERAL_COL_MAP[clean]
        df = df.rename(columns=rename)

        results: list[ObservedCollateral] = []

        for _, row in df.iterrows():
            try:
                raw_asset = _safe_str(row.get("collateral_asset"), "") or ""
                if not raw_asset:
                    continue
                # Skip non-standard / illiquid claim tokens
                if "FTX-Claims" in raw_asset:
                    continue

                canon_asset = normalize_creditdesk_symbol(raw_asset)
                units = _safe_float(row.get("units_posted"))

                results.append(ObservedCollateral(
                    asof_date=asof_date,
                    customer_id=int(_safe_float(row.get("customer_id"))),
                    counterparty_name=_safe_str(
                        row.get("counterparty_name"), "",
                    ) or "",
                    collateral_relationship=_safe_str(
                        row.get("collateral_relationship"), "",
                    ) or "",
                    collateral_asset=canon_asset,
                    units_posted=units,
                    data_source=_safe_str(row.get("data_source"), "creditdesk") or "creditdesk",
                    is_tri_party=_safe_bool(row.get("is_tri_party")),
                    custodial_venue=_safe_str(
                        row.get("custodial_venue"), "",
                    ) or "",
                ))
            except Exception:
                log.debug("book_import_skip_collateral_row", exc_info=True)
                continue

        return results

    # ── Collateral allocation ──────────────────────────────────────

    def _run_collateral_allocation(
        self,
        positions: list[BookPosition],
        collateral: list[ObservedCollateral],
        asset_prices: dict[str, float],
    ) -> dict[int, list[AllocatedCollateral]]:
        """Run pro-rata allocation for all counterparties."""
        # Group by customer_id
        loans_by_cust: dict[int, list[BookPosition]] = defaultdict(list)
        for p in positions:
            loans_by_cust[p.customer_id].append(p)

        col_by_cust: dict[int, list[ObservedCollateral]] = defaultdict(list)
        for c in collateral:
            col_by_cust[c.customer_id].append(c)

        all_allocations: dict[int, list[AllocatedCollateral]] = {}

        for cust_id in set(loans_by_cust) | set(col_by_cust):
            cust_loans = loans_by_cust.get(cust_id, [])
            cust_col = col_by_cust.get(cust_id, [])
            if cust_loans and cust_col:
                allocs = allocate_collateral(cust_loans, cust_col, asset_prices)
                all_allocations.update(allocs)

        return all_allocations

    # ── Market matching ────────────────────────────────────────────

    async def _match_defi_to_market(
        self,
        db: AsyncSession,
        positions: list[BookPosition],
    ) -> None:
        """Match DeFi positions to current market opportunities."""
        for pos in positions:
            if pos.category not in (
                PositionCategory.DEFI_SUPPLY,
                PositionCategory.DEFI_BORROW,
            ):
                continue

            side = (
                "SUPPLY"
                if pos.category == PositionCategory.DEFI_SUPPLY
                else "BORROW"
            )

            market_rate = await self._find_market_rate(
                db,
                protocol_name=pos.protocol_name,
                chain=pos.protocol_chain,
                asset_id=pos.principal_asset,
                side=side,
            )
            if market_rate is not None:
                pos.current_market_rate_pct = market_rate
                pos.rate_vs_market_bps = round(
                    (pos.interest_rate_pct - market_rate) * 100.0, 2,
                )

    async def _find_market_rate(
        self,
        db: AsyncSession,
        *,
        protocol_name: str | None,
        chain: str | None,
        asset_id: str,
        side: str,
    ) -> float | None:
        """Find the market rate for a DeFi position from opportunities."""
        if not protocol_name:
            return None

        # Build protocol slug from name
        slug = protocol_name.lower().replace(" ", "-").replace("_", "-")

        q = (
            select(MarketOpportunityRow.total_apy_pct)
            .where(MarketOpportunityRow.side == side)
            .where(MarketOpportunityRow.asset_id == asset_id)
        )

        # Try exact protocol match first
        q_exact = q.where(
            MarketOpportunityRow.protocol_slug.ilike(f"%{slug}%")
        )
        if chain:
            q_exact = q_exact.where(MarketOpportunityRow.chain == chain)

        result = (await db.execute(q_exact.limit(1))).scalar_one_or_none()
        if result is not None:
            return result

        # Fallback: match by protocol name pattern
        q_fallback = q.where(
            MarketOpportunityRow.protocol.ilike(f"%{protocol_name}%")
        )
        if chain:
            q_fallback = q_fallback.where(MarketOpportunityRow.chain == chain)

        result = (await db.execute(q_fallback.limit(1))).scalar_one_or_none()
        return result

    # ── Persistence ────────────────────────────────────────────────

    async def _persist(
        self,
        db: AsyncSession,
        book_id: str,
        name: str,
        source_file: str,
        import_date: datetime,
        as_of_date: datetime | None,
        positions: list[BookPosition],
        collateral: list[ObservedCollateral],
        allocations: dict[int, list[AllocatedCollateral]],
        summary: BookSummary,
    ) -> None:
        """Write all book data to the database."""
        import dataclasses

        # Book row
        book_row = BookRow(
            book_id=book_id,
            name=name,
            source_file=source_file,
            import_date=import_date,
            as_of_date=as_of_date,
            total_positions=len(positions),
            summary=dataclasses.asdict(summary),
        )
        db.add(book_row)
        await db.flush()

        # Position rows
        for pos in positions:
            db.add(BookPositionRow(
                book_id=book_id,
                loan_id=pos.loan_id,
                customer_id=pos.customer_id,
                counterparty_name=pos.counterparty_name,
                counterparty_legal_entity=pos.counterparty_legal_entity,
                category=pos.category.value,
                direction=pos.direction,
                principal_asset=pos.principal_asset,
                principal_qty=pos.principal_qty,
                principal_usd=pos.principal_usd,
                effective_date=pos.effective_date,
                maturity_date=pos.maturity_date,
                tenor=pos.tenor,
                recall_period_days=pos.recall_period_days,
                collateral_assets_raw=pos.collateral_assets_raw,
                initial_collateralization_ratio_pct=pos.initial_collateralization_ratio_pct,
                rehypothecation_allowed=pos.rehypothecation_allowed,
                collateral_substitution_allowed=pos.collateral_substitution_allowed,
                is_collateralized=pos.is_collateralized,
                loan_type=pos.loan_type,
                interest_rate_pct=pos.interest_rate_pct,
                status=pos.status,
                query_notes=pos.query_notes,
                protocol_name=pos.protocol_name,
                protocol_chain=pos.protocol_chain,
                umbrella_group=pos.umbrella_group,
                matched_opportunity_id=pos.matched_opportunity_id,
                current_market_rate_pct=pos.current_market_rate_pct,
                rate_vs_market_bps=pos.rate_vs_market_bps,
            ))

        # Collateral observations
        for col in collateral:
            db.add(BookObservedCollateralRow(
                book_id=book_id,
                asof_date=col.asof_date,
                customer_id=col.customer_id,
                counterparty_name=col.counterparty_name,
                collateral_relationship=col.collateral_relationship,
                collateral_asset=col.collateral_asset,
                units_posted=col.units_posted,
                data_source=col.data_source,
                is_tri_party=col.is_tri_party,
                custodial_venue=col.custodial_venue,
            ))

        # Collateral allocations
        for loan_id, alloc_list in allocations.items():
            for alloc in alloc_list:
                db.add(BookCollateralAllocationRow(
                    book_id=book_id,
                    loan_id=loan_id,
                    collateral_asset=alloc.collateral_asset,
                    allocated_units=alloc.allocated_units,
                    allocated_usd=alloc.allocated_usd,
                    allocation_weight_pct=alloc.allocation_weight_pct,
                ))

        await db.commit()

    # ── Row serialization ──────────────────────────────────────────

    @staticmethod
    def _position_row_to_dict(row: BookPositionRow) -> dict:
        return {
            "loan_id": row.loan_id,
            "customer_id": row.customer_id,
            "counterparty_name": row.counterparty_name,
            "counterparty_legal_entity": row.counterparty_legal_entity,
            "category": row.category,
            "direction": row.direction,
            "principal_asset": row.principal_asset,
            "principal_qty": row.principal_qty,
            "principal_usd": row.principal_usd,
            "effective_date": row.effective_date.isoformat()
            if row.effective_date
            else None,
            "maturity_date": row.maturity_date.isoformat()
            if row.maturity_date
            else None,
            "tenor": row.tenor,
            "recall_period_days": row.recall_period_days,
            "collateral_assets_raw": row.collateral_assets_raw,
            "initial_collateralization_ratio_pct": row.initial_collateralization_ratio_pct,
            "rehypothecation_allowed": row.rehypothecation_allowed,
            "collateral_substitution_allowed": row.collateral_substitution_allowed,
            "is_collateralized": row.is_collateralized,
            "loan_type": row.loan_type,
            "interest_rate_pct": row.interest_rate_pct,
            "status": row.status,
            "query_notes": row.query_notes,
            "protocol_name": row.protocol_name,
            "protocol_chain": row.protocol_chain,
            "umbrella_group": row.umbrella_group,
            "matched_opportunity_id": row.matched_opportunity_id,
            "current_market_rate_pct": row.current_market_rate_pct,
            "rate_vs_market_bps": row.rate_vs_market_bps,
        }
