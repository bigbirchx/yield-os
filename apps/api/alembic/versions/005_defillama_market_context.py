"""add defillama free-tier market context tables

Revision ID: 005
Revises: 004
Create Date: 2026-03-27

Creates seven tables for the DefiLlama free-tier data layer:
  - defillama_yield_pool_snapshot
  - defillama_yield_pool_history
  - defillama_protocol_snapshot
  - defillama_chain_tvl_history
  - defillama_stablecoin_snapshot
  - defillama_stablecoin_history
  - defillama_market_context_snapshot

All use source_name='defillama_free'. No Pro-only endpoints used.
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- defillama_yield_pool_snapshot --------------------------------------
    op.create_table(
        "defillama_yield_pool_snapshot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pool_id", sa.String(128), nullable=False),
        sa.Column("project", sa.String(128), nullable=False),
        sa.Column("chain", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(64), nullable=False),
        sa.Column("tvl_usd", sa.Float(), nullable=True),
        sa.Column("apy", sa.Float(), nullable=True),
        sa.Column("apy_base", sa.Float(), nullable=True),
        sa.Column("apy_reward", sa.Float(), nullable=True),
        sa.Column("stablecoin", sa.Boolean(), nullable=True),
        sa.Column("il_risk", sa.String(32), nullable=True),
        sa.Column("exposure", sa.String(64), nullable=True),
        sa.Column("predictions", sa.JSON(), nullable=True),
        sa.Column("source_name", sa.String(64), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dl_yps_snapshot_at", "defillama_yield_pool_snapshot", ["snapshot_at"])
    op.create_index("ix_dl_yps_pool_id", "defillama_yield_pool_snapshot", ["pool_id"])
    op.create_index("ix_dl_yps_symbol", "defillama_yield_pool_snapshot", ["symbol"])

    # -- defillama_yield_pool_history ---------------------------------------
    op.create_table(
        "defillama_yield_pool_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pool_id", sa.String(128), nullable=False),
        sa.Column("apy", sa.Float(), nullable=True),
        sa.Column("tvl_usd", sa.Float(), nullable=True),
        sa.Column("apy_base", sa.Float(), nullable=True),
        sa.Column("apy_reward", sa.Float(), nullable=True),
        sa.Column("source_name", sa.String(64), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dl_yph_ts", "defillama_yield_pool_history", ["ts"])
    op.create_index("ix_dl_yph_pool_id", "defillama_yield_pool_history", ["pool_id"])

    # -- defillama_protocol_snapshot ----------------------------------------
    op.create_table(
        "defillama_protocol_snapshot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("protocol_slug", sa.String(128), nullable=False),
        sa.Column("protocol_name", sa.String(256), nullable=False),
        sa.Column("category", sa.String(64), nullable=True),
        sa.Column("chain", sa.String(64), nullable=True),
        sa.Column("tvl_usd", sa.Float(), nullable=True),
        sa.Column("change_1d", sa.Float(), nullable=True),
        sa.Column("change_7d", sa.Float(), nullable=True),
        sa.Column("change_1m", sa.Float(), nullable=True),
        sa.Column("source_name", sa.String(64), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dl_ps_ts", "defillama_protocol_snapshot", ["ts"])
    op.create_index("ix_dl_ps_slug", "defillama_protocol_snapshot", ["protocol_slug"])

    # -- defillama_chain_tvl_history ----------------------------------------
    op.create_table(
        "defillama_chain_tvl_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("chain", sa.String(64), nullable=False),
        sa.Column("tvl_usd", sa.Float(), nullable=False),
        sa.Column("source_name", sa.String(64), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dl_cth_ts", "defillama_chain_tvl_history", ["ts"])
    op.create_index("ix_dl_cth_chain", "defillama_chain_tvl_history", ["chain"])

    # -- defillama_stablecoin_snapshot --------------------------------------
    op.create_table(
        "defillama_stablecoin_snapshot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stablecoin_id", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("circulating_usd", sa.Float(), nullable=True),
        sa.Column("chains", sa.JSON(), nullable=True),
        sa.Column("peg_type", sa.String(32), nullable=True),
        sa.Column("peg_mechanism", sa.String(64), nullable=True),
        sa.Column("source_name", sa.String(64), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dl_ss_ts", "defillama_stablecoin_snapshot", ["ts"])
    op.create_index("ix_dl_ss_id", "defillama_stablecoin_snapshot", ["stablecoin_id"])
    op.create_index("ix_dl_ss_symbol", "defillama_stablecoin_snapshot", ["symbol"])

    # -- defillama_stablecoin_history ---------------------------------------
    op.create_table(
        "defillama_stablecoin_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("chain", sa.String(64), nullable=True),
        sa.Column("circulating_usd", sa.Float(), nullable=True),
        sa.Column("source_name", sa.String(64), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dl_sh_ts", "defillama_stablecoin_history", ["ts"])
    op.create_index("ix_dl_sh_chain", "defillama_stablecoin_history", ["chain"])

    # -- defillama_market_context_snapshot ----------------------------------
    op.create_table(
        "defillama_market_context_snapshot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("context_type", sa.String(32), nullable=False),
        sa.Column("protocol_or_chain", sa.String(128), nullable=False),
        sa.Column("metric_name", sa.String(64), nullable=False),
        sa.Column("metric_value", sa.Float(), nullable=True),
        sa.Column("source_name", sa.String(64), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dl_mcs_ts", "defillama_market_context_snapshot", ["ts"])
    op.create_index("ix_dl_mcs_type", "defillama_market_context_snapshot", ["context_type"])
    op.create_index("ix_dl_mcs_proto", "defillama_market_context_snapshot", ["protocol_or_chain"])


def downgrade() -> None:
    op.drop_table("defillama_market_context_snapshot")
    op.drop_table("defillama_stablecoin_history")
    op.drop_table("defillama_stablecoin_snapshot")
    op.drop_table("defillama_chain_tvl_history")
    op.drop_table("defillama_protocol_snapshot")
    op.drop_table("defillama_yield_pool_history")
    op.drop_table("defillama_yield_pool_snapshot")
