"""add coingecko reference tables

Revision ID: 004
Revises: 003
Create Date: 2026-03-14

Creates four tables for the CoinGecko market-reference layer:
  - asset_reference_map
  - market_reference_snapshots
  - market_reference_history
  - api_usage_snapshots
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── asset_reference_map ──────────────────────────────────────────────
    op.create_table(
        "asset_reference_map",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("canonical_symbol", sa.String(32), nullable=False),
        sa.Column("coingecko_id", sa.String(128), nullable=True),
        sa.Column("name", sa.String(256), nullable=True),
        sa.Column("asset_type", sa.String(64), nullable=True),
        sa.Column("chain", sa.String(64), nullable=True),
        sa.Column("contract_address", sa.String(256), nullable=True),
        sa.Column("source_name", sa.String(64), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "source_name", name="uq_asset_ref_symbol_source"),
    )
    op.create_index("ix_asset_ref_symbol", "asset_reference_map", ["symbol"])
    op.create_index("ix_asset_ref_coingecko_id", "asset_reference_map", ["coingecko_id"])

    # ── market_reference_snapshots ───────────────────────────────────────
    op.create_table(
        "market_reference_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("coingecko_id", sa.String(128), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("current_price_usd", sa.Float(), nullable=True),
        sa.Column("market_cap_usd", sa.Float(), nullable=True),
        sa.Column("fully_diluted_valuation_usd", sa.Float(), nullable=True),
        sa.Column("volume_24h_usd", sa.Float(), nullable=True),
        sa.Column("circulating_supply", sa.Float(), nullable=True),
        sa.Column("total_supply", sa.Float(), nullable=True),
        sa.Column("max_supply", sa.Float(), nullable=True),
        sa.Column("price_change_24h_pct", sa.Float(), nullable=True),
        sa.Column("source_name", sa.String(64), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mkt_snap_snapshot_at", "market_reference_snapshots", ["snapshot_at"])
    op.create_index("ix_mkt_snap_coingecko_id", "market_reference_snapshots", ["coingecko_id"])
    op.create_index("ix_mkt_snap_symbol", "market_reference_snapshots", ["symbol"])

    # ── market_reference_history ─────────────────────────────────────────
    op.create_table(
        "market_reference_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("coingecko_id", sa.String(128), nullable=False),
        sa.Column("price_usd", sa.Float(), nullable=True),
        sa.Column("market_cap_usd", sa.Float(), nullable=True),
        sa.Column("volume_24h_usd", sa.Float(), nullable=True),
        sa.Column("source_name", sa.String(64), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mkt_hist_snapshot_at", "market_reference_history", ["snapshot_at"])
    op.create_index("ix_mkt_hist_coingecko_id", "market_reference_history", ["coingecko_id"])
    op.create_index("ix_mkt_hist_coin_at", "market_reference_history", ["coingecko_id", "snapshot_at"])

    # ── api_usage_snapshots ──────────────────────────────────────────────
    op.create_table(
        "api_usage_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("rate_limit", sa.Integer(), nullable=True),
        sa.Column("remaining_credits", sa.Integer(), nullable=True),
        sa.Column("monthly_total_credits", sa.Integer(), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_api_usage_snapshot_at", "api_usage_snapshots", ["snapshot_at"])


def downgrade() -> None:
    op.drop_table("api_usage_snapshots")
    op.drop_table("market_reference_history")
    op.drop_table("market_reference_snapshots")
    op.drop_table("asset_reference_map")
