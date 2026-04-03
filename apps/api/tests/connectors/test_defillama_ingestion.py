"""
Unit tests for DeFiLlama ingestion normalization logic.
No DB or HTTP involved — tests _lending_row() and _staking_row() directly.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.connectors.defillama_client import DeFiLlamaPool
from app.services.defillama_ingestion import (
    STAKING_UNDERLYING,
    _lending_row,
    _staking_row,
)


def _make_pool(**kwargs) -> DeFiLlamaPool:
    defaults = dict(
        pool="aa-bb-cc-1",
        chain="Ethereum",
        project="aave-v3",
        symbol="USDC",
        tvlUsd=1_500_000_000.0,
        apy=5.2,
        apyBase=4.8,
        apyReward=0.4,
        apyBaseBorrow=6.1,
        apyRewardBorrow=0.2,
        totalSupplyUsd=2_000_000_000.0,
        totalBorrowUsd=1_500_000_000.0,
        ltv=0.77,
        poolMeta=None,
        underlyingTokens=[],
        rewardTokens=[],
    )
    defaults.update(kwargs)
    return DeFiLlamaPool.model_validate(defaults)


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Lending row normalization
# ---------------------------------------------------------------------------


def test_lending_row_maps_all_fields():
    pool = _make_pool()
    row = _lending_row(pool, _now())

    assert row.symbol == "USDC"
    assert row.protocol == "aave-v3"
    assert row.market == "aa-bb-cc-1"
    assert row.chain == "Ethereum"
    assert row.pool_id == "aa-bb-cc-1"
    assert row.supply_apy == pytest.approx(4.8)
    assert row.borrow_apy == pytest.approx(6.1)
    assert row.reward_supply_apy == pytest.approx(0.4)
    assert row.reward_borrow_apy == pytest.approx(0.2)
    assert row.tvl_usd == pytest.approx(1_500_000_000.0)
    assert row.utilization == pytest.approx(0.75)
    assert row.available_liquidity_usd == pytest.approx(500_000_000.0)


def test_lending_row_preserves_raw_payload():
    pool = _make_pool()
    row = _lending_row(pool, _now())

    assert row.raw_payload is not None
    assert row.raw_payload["pool"] == "aa-bb-cc-1"
    assert row.raw_payload["project"] == "aave-v3"
    assert row.raw_payload["apy_base"] == pytest.approx(4.8)


def test_lending_row_handles_null_borrow_apy():
    pool = _make_pool(apyBaseBorrow=None)
    row = _lending_row(pool, _now())

    assert row.borrow_apy is None


def test_lending_row_symbol_uppercased():
    pool = _make_pool(symbol="wbtc")
    row = _lending_row(pool, _now())

    assert row.symbol == "WBTC"


# ---------------------------------------------------------------------------
# Staking row normalization
# ---------------------------------------------------------------------------


def test_staking_row_lido():
    pool = _make_pool(
        pool="steth-pool",
        project="lido",
        symbol="STETH",
        chain="Ethereum",
        apyBase=3.8,
        apyReward=None,
        apy=3.8,
        apyBaseBorrow=None,
        totalSupplyUsd=None,
        totalBorrowUsd=None,
    )
    row = _staking_row(pool, _now())

    assert row.symbol == "stETH"  # normalised to canonical ID
    assert row.underlying_symbol == "ETH"
    assert row.protocol == "lido"
    assert row.chain == "Ethereum"
    assert row.pool_id == "steth-pool"
    assert row.staking_apy == pytest.approx(3.8)
    assert row.base_apy == pytest.approx(3.8)
    assert row.reward_apy is None


def test_staking_row_underlying_mapping():
    for symbol, expected_underlying in STAKING_UNDERLYING.items():
        pool = _make_pool(symbol=symbol, project="lido", apy=3.5, apyBase=3.5)
        row = _staking_row(pool, _now())
        assert row.underlying_symbol == expected_underlying, (
            f"{symbol} -> expected {expected_underlying}, got {row.underlying_symbol}"
        )


def test_staking_row_preserves_raw_payload():
    pool = _make_pool(project="lido", symbol="STETH")
    row = _staking_row(pool, _now())

    assert row.raw_payload is not None
    assert row.raw_payload["project"] == "lido"
