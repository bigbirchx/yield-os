"""add market opportunities tables

Revision ID: 007
Revises: 006
Create Date: 2026-04-02

Creates the unified opportunity storage layer:
  - market_opportunities        — latest state per opportunity (upserted)
  - market_opportunity_snapshots — time-series rate history
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── market_opportunities ────────────────────────────────────────────────
    op.create_table(
        "market_opportunities",
        # Identity
        sa.Column("opportunity_id", sa.String(512), nullable=False),
        sa.Column("venue", sa.String(64), nullable=False),
        sa.Column("chain", sa.String(64), nullable=False),
        sa.Column("protocol", sa.String(128), nullable=False),
        sa.Column("protocol_slug", sa.String(128), nullable=False),
        sa.Column("market_id", sa.String(512), nullable=False),
        sa.Column("market_name", sa.String(256), nullable=True),
        sa.Column("side", sa.String(16), nullable=False),
        sa.Column("asset_id", sa.String(64), nullable=False),
        sa.Column("asset_symbol", sa.String(64), nullable=False),
        sa.Column("umbrella_group", sa.String(32), nullable=False),
        sa.Column("asset_sub_type", sa.String(64), nullable=False),
        sa.Column("opportunity_type", sa.String(64), nullable=False),
        sa.Column("effective_duration", sa.String(32), nullable=False),
        sa.Column("maturity_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("days_to_maturity", sa.Float(), nullable=True),
        # Yield
        sa.Column("total_apy_pct", sa.Float(), nullable=False),
        sa.Column("base_apy_pct", sa.Float(), nullable=False),
        sa.Column("reward_breakdown", JSONB(), nullable=False, server_default="[]"),
        # Size and capacity
        sa.Column("total_supplied", sa.Float(), nullable=True),
        sa.Column("total_supplied_usd", sa.Float(), nullable=True),
        sa.Column("total_borrowed", sa.Float(), nullable=True),
        sa.Column("total_borrowed_usd", sa.Float(), nullable=True),
        sa.Column("capacity_cap", sa.Float(), nullable=True),
        sa.Column("capacity_remaining", sa.Float(), nullable=True),
        sa.Column("is_capacity_capped", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("tvl_usd", sa.Float(), nullable=True),
        # Liquidity and exit risk
        sa.Column("liquidity", JSONB(), nullable=False, server_default="{}"),
        # Rate model
        sa.Column("rate_model", JSONB(), nullable=True),
        # Collateral (supply side)
        sa.Column("is_collateral_eligible", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("as_collateral_max_ltv_pct", sa.Float(), nullable=True),
        sa.Column("as_collateral_liquidation_ltv_pct", sa.Float(), nullable=True),
        # Collateral matrix (borrow side)
        sa.Column("collateral_options", JSONB(), nullable=True),
        # Receipt token
        sa.Column("receipt_token", JSONB(), nullable=True),
        # Filtering tags
        sa.Column("is_amm_lp", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_pendle", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("pendle_type", sa.String(16), nullable=True),
        sa.Column("tags", JSONB(), nullable=False, server_default="[]"),
        # Metadata
        sa.Column("data_source", sa.String(128), nullable=False),
        sa.Column("last_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_freshness_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_url", sa.String(512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("opportunity_id"),
    )
    # Single-column indexes
    op.create_index("ix_mo_venue", "market_opportunities", ["venue"])
    op.create_index("ix_mo_chain", "market_opportunities", ["chain"])
    op.create_index("ix_mo_protocol", "market_opportunities", ["protocol"])
    op.create_index("ix_mo_side", "market_opportunities", ["side"])
    op.create_index("ix_mo_asset_id", "market_opportunities", ["asset_id"])
    op.create_index("ix_mo_umbrella_group", "market_opportunities", ["umbrella_group"])
    op.create_index("ix_mo_asset_sub_type", "market_opportunities", ["asset_sub_type"])
    op.create_index("ix_mo_opportunity_type", "market_opportunities", ["opportunity_type"])
    op.create_index("ix_mo_total_apy_desc", "market_opportunities", [sa.text("total_apy_pct DESC")])
    op.create_index("ix_mo_last_updated_at", "market_opportunities", ["last_updated_at"])
    op.create_index("ix_mo_is_amm_lp", "market_opportunities", ["is_amm_lp"])
    op.create_index("ix_mo_is_pendle", "market_opportunities", ["is_pendle"])
    # Composite indexes
    op.create_index("ix_mo_umbrella_side_type", "market_opportunities", ["umbrella_group", "side", "opportunity_type"])
    op.create_index("ix_mo_asset_side", "market_opportunities", ["asset_id", "side"])
    op.create_index("ix_mo_venue_chain", "market_opportunities", ["venue", "chain"])

    # ── market_opportunity_snapshots ────────────────────────────────────────
    op.create_table(
        "market_opportunity_snapshots",
        sa.Column("id", sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column("opportunity_id", sa.String(512), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_apy_pct", sa.Float(), nullable=False),
        sa.Column("base_apy_pct", sa.Float(), nullable=False),
        sa.Column("total_supplied", sa.Float(), nullable=True),
        sa.Column("total_supplied_usd", sa.Float(), nullable=True),
        sa.Column("total_borrowed", sa.Float(), nullable=True),
        sa.Column("total_borrowed_usd", sa.Float(), nullable=True),
        sa.Column("utilization_rate_pct", sa.Float(), nullable=True),
        sa.Column("tvl_usd", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["opportunity_id"],
            ["market_opportunities.opportunity_id"],
            name="fk_mos_opportunity_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mos_snapshot_at", "market_opportunity_snapshots", ["snapshot_at"])
    op.create_index(
        "ix_mos_opp_snapshot",
        "market_opportunity_snapshots",
        ["opportunity_id", sa.text("snapshot_at DESC")],
    )


def downgrade() -> None:
    op.drop_table("market_opportunity_snapshots")
    op.drop_table("market_opportunities")
