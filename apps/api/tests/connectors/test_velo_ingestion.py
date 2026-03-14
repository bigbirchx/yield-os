"""
Tests for velo_ingestion normalization logic.

DB is not involved — we test _normalize() directly.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.connectors.velo_client import VeloSnapshot
from app.services.velo_ingestion import _normalize


def _make_snapshot(**kwargs) -> VeloSnapshot:
    defaults = dict(
        coin="BTC",
        venue="Binance",
        funding_rate=0.0001,
        open_interest_usd=5_000_000_000.0,
        basis_annualized=0.08,
        mark_price=95_000.0,
        index_price=94_980.0,
        spot_volume_usd=1_200_000_000.0,
        perp_volume_usd=8_000_000_000.0,
        raw_funding={"venue": "Binance", "coin": "BTC", "funding_rate": 0.0001},
        raw_oi={"venue": "Binance", "coin": "BTC", "open_interest_usd": 5_000_000_000.0},
        raw_summary={"venue": "Binance", "coin": "BTC", "mark_price": 95_000.0},
    )
    defaults.update(kwargs)
    return VeloSnapshot(**defaults)


def test_normalize_maps_all_fields():
    snap = _make_snapshot()
    now = datetime.now(UTC)
    row = _normalize(snap, now)

    assert row.symbol == "BTC"
    assert row.venue == "Binance"
    assert row.funding_rate == pytest.approx(0.0001)
    assert row.open_interest_usd == pytest.approx(5_000_000_000.0)
    assert row.basis_annualized == pytest.approx(0.08)
    assert row.mark_price == pytest.approx(95_000.0)
    assert row.index_price == pytest.approx(94_980.0)
    assert row.spot_volume_usd == pytest.approx(1_200_000_000.0)
    assert row.perp_volume_usd == pytest.approx(8_000_000_000.0)


def test_normalize_preserves_raw_payloads():
    snap = _make_snapshot()
    row = _normalize(snap, datetime.now(UTC))

    assert row.raw_payload is not None
    assert "funding" in row.raw_payload
    assert "open_interest" in row.raw_payload
    assert "market_summary" in row.raw_payload
    assert row.raw_payload["funding"]["funding_rate"] == pytest.approx(0.0001)


def test_normalize_handles_none_fields():
    snap = _make_snapshot(funding_rate=None, open_interest_usd=None, raw_oi=None)
    row = _normalize(snap, datetime.now(UTC))

    assert row.funding_rate is None
    assert row.open_interest_usd is None
    assert row.raw_payload["open_interest"] is None
