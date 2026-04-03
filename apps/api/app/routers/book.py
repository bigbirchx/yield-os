"""
Portfolio book endpoints.

Upload a CreditDesk WACC Export workbook, query positions,
collateral, counterparties, market comparisons, and optimization.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Body, Depends, File, Query, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.book_import import BookImportService
from app.services.book_optimizer import BookOptimizer

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/book", tags=["book"])

_svc = BookImportService()
_optimizer = BookOptimizer()


# ── POST /api/book/import ─────────────────────────────────────────

@router.post("/import")
async def import_book(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload a CreditDesk WACC Export Excel file and import it."""
    contents = await file.read()
    result = await _svc.import_from_upload(db, contents, file.filename or "upload.xlsx")
    return result


# ── GET /api/book/{book_id} ───────────────────────────────────────

@router.get("/{book_id}")
async def get_book(
    book_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Return full book metadata with summary."""
    data = await _svc.get_book(db, book_id)
    if data is None:
        return JSONResponse(status_code=404, content={"detail": "Book not found"})
    return data


# ── GET /api/book/{book_id}/positions ─────────────────────────────

@router.get("/{book_id}/positions")
async def get_positions(
    book_id: str,
    category: str | None = Query(None, description="Filter by category, e.g. DEFI_SUPPLY"),
    asset: str | None = Query(None, description="Filter by principal asset"),
    counterparty: str | None = Query(None, description="Search counterparty name"),
    min_rate: float | None = Query(None, description="Minimum interest rate %"),
    db: AsyncSession = Depends(get_db),
):
    """Return positions with optional filters."""
    return await _svc.get_positions(
        db, book_id,
        category=category,
        asset=asset,
        counterparty=counterparty,
        min_rate=min_rate,
    )


# ── GET /api/book/{book_id}/defi ──────────────────────────────────

@router.get("/{book_id}/defi")
async def get_defi_positions(
    book_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Return DeFi/staking positions with market rate comparisons."""
    return await _svc.get_defi_positions(db, book_id)


# ── GET /api/book/{book_id}/collateral ────────────────────────────

@router.get("/{book_id}/collateral")
async def get_collateral(
    book_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Return observed collateral and pro-rata allocations."""
    return await _svc.get_collateral(db, book_id)


# ── GET /api/book/{book_id}/counterparty/{customer_id} ───────────

@router.get("/{book_id}/counterparty/{customer_id}")
async def get_counterparty_view(
    book_id: str,
    customer_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Single counterparty: positions + collateral + allocations."""
    return await _svc.get_counterparty_view(db, book_id, customer_id)


# ── GET /api/book/{book_id}/summary ───────────────────────────────

@router.get("/{book_id}/summary")
async def get_summary(
    book_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Return computed BookSummary for a book."""
    data = await _svc.get_summary(db, book_id)
    if data is None:
        return JSONResponse(status_code=404, content={"detail": "Book not found"})
    return data


# ── POST /api/book/{book_id}/refresh-matching ─────────────────────

@router.post("/{book_id}/refresh-matching")
async def refresh_matching(
    book_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Re-match DeFi positions to current market rates."""
    return await _svc.refresh_market_matching(db, book_id)


# ── Optimizer endpoints ───────────────────────────────────────────


class AnalyzeConfig(BaseModel):
    min_improvement_bps: int = Field(default=50, ge=0)
    holding_period_days: int = Field(default=30, ge=1, le=365)
    max_ltv_pct: float = Field(default=70.0, ge=0, le=100)
    include_conversion_routes: bool = True
    bilateral_comparison: bool = True
    maturity_warning_days: int = Field(default=14, ge=1)
    capacity_warning_pct: float = Field(default=85.0, ge=0, le=100)
    max_suggestions: int = Field(default=30, ge=1, le=100)


class AnalyzeRequest(BaseModel):
    config: AnalyzeConfig = Field(default_factory=AnalyzeConfig)


@router.post("/{book_id}/analyze")
async def analyze_book(
    book_id: str,
    body: AnalyzeRequest = Body(default_factory=AnalyzeRequest),
    db: AsyncSession = Depends(get_db),
):
    """Run full book optimization analysis. Returns prioritised suggestions."""
    return await _optimizer.analyze_book(db, book_id, config=body.config.model_dump())


@router.get("/{book_id}/defi-vs-market")
async def defi_vs_market(
    book_id: str,
    db: AsyncSession = Depends(get_db),
):
    """DeFi rate comparison table: our rate vs current market for each position."""
    return await _optimizer.defi_vs_market_comparison(db, book_id)


@router.get("/{book_id}/bilateral-pricing")
async def bilateral_pricing(
    book_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Bilateral pricing report: are we pricing loans appropriately vs DeFi?"""
    return await _optimizer.bilateral_pricing_report(db, book_id)


@router.get("/{book_id}/collateral-efficiency")
async def collateral_efficiency(
    book_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Per-counterparty collateral efficiency: excess, rehypothecation, potential yield."""
    return await _optimizer.collateral_efficiency_report(db, book_id)


@router.get("/{book_id}/maturity-calendar")
async def maturity_calendar(
    book_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Upcoming maturities sorted by date with market rate comparisons."""
    return await _optimizer.maturity_calendar(db, book_id)
