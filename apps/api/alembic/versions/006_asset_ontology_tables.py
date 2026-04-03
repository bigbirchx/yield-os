"""add asset ontology tables

Revision ID: 006
Revises: 005
Create Date: 2026-04-02

Creates four tables that mirror the in-memory asset-registry package:
  - asset_definitions       — canonical asset taxonomy
  - asset_chain_deployments — per-chain deployment details (many-to-many)
  - venue_symbol_mappings   — normalisation table (venue raw symbol → canonical)
  - conversion_edges        — conversion cost/path graph
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── asset_definitions ────────────────────────────────────────────────────
    op.create_table(
        "asset_definitions",
        sa.Column("canonical_id", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("umbrella", sa.String(32), nullable=False),
        sa.Column("sub_type", sa.String(64), nullable=False),
        sa.Column("fungibility", sa.String(32), nullable=False),
        sa.Column("coingecko_id", sa.String(128), nullable=True),
        # Self-referential FK — DEFERRABLE so bulk upserts land in one txn
        sa.Column("underlying_asset_id", sa.String(32), nullable=True),
        sa.Column("tags", JSONB(), nullable=True),
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
        sa.PrimaryKeyConstraint("canonical_id"),
        sa.ForeignKeyConstraint(
            ["underlying_asset_id"],
            ["asset_definitions.canonical_id"],
            name="fk_asset_def_underlying",
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    op.create_index("ix_asset_def_umbrella", "asset_definitions", ["umbrella"])
    op.create_index("ix_asset_def_coingecko_id", "asset_definitions", ["coingecko_id"])

    # ── asset_chain_deployments ──────────────────────────────────────────────
    op.create_table(
        "asset_chain_deployments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("canonical_id", sa.String(32), nullable=False),
        sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("contract_address", sa.String(256), nullable=True),
        sa.Column("decimals", sa.Integer(), nullable=False, server_default="18"),
        sa.Column("is_native", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["canonical_id"],
            ["asset_definitions.canonical_id"],
            name="fk_acd_canonical_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canonical_id", "chain", name="uq_asset_chain"),
    )
    op.create_index("ix_acd_canonical_id", "asset_chain_deployments", ["canonical_id"])
    op.create_index("ix_acd_chain", "asset_chain_deployments", ["chain"])

    # ── venue_symbol_mappings ────────────────────────────────────────────────
    # chain is '' (empty string) for chain-agnostic mappings — never NULL —
    # so the unique constraint (venue, venue_symbol, chain) works correctly.
    op.create_table(
        "venue_symbol_mappings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("venue", sa.String(64), nullable=False),
        sa.Column("venue_symbol", sa.String(128), nullable=False),
        sa.Column("canonical_id", sa.String(32), nullable=False),
        sa.Column("chain", sa.String(32), nullable=False, server_default=""),
        sa.Column(
            "is_contract_address",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column("notes", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["canonical_id"],
            ["asset_definitions.canonical_id"],
            name="fk_vsm_canonical_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("venue", "venue_symbol", "chain", name="uq_venue_symbol_chain"),
    )
    op.create_index("ix_vsm_venue", "venue_symbol_mappings", ["venue"])
    op.create_index("ix_vsm_canonical_id", "venue_symbol_mappings", ["canonical_id"])

    # ── conversion_edges ─────────────────────────────────────────────────────
    op.create_table(
        "conversion_edges",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("from_asset", sa.String(32), nullable=False),
        sa.Column("to_asset", sa.String(32), nullable=False),
        sa.Column("method", sa.String(32), nullable=False),
        sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("protocol", sa.String(128), nullable=True),
        sa.Column("estimated_gas_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("fee_bps", sa.Float(), nullable=False, server_default="0"),
        sa.Column("slippage_bps_estimate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("min_duration_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_duration_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_deterministic", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("capacity_limited", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("notes", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["from_asset"],
            ["asset_definitions.canonical_id"],
            name="fk_ce_from_asset",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["to_asset"],
            ["asset_definitions.canonical_id"],
            name="fk_ce_to_asset",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "from_asset", "to_asset", "method", "chain",
            name="uq_conversion_edge",
        ),
    )
    op.create_index("ix_ce_from_asset", "conversion_edges", ["from_asset"])
    op.create_index("ix_ce_to_asset", "conversion_edges", ["to_asset"])
    op.create_index("ix_ce_chain", "conversion_edges", ["chain"])


def downgrade() -> None:
    op.drop_table("conversion_edges")
    op.drop_table("venue_symbol_mappings")
    op.drop_table("asset_chain_deployments")
    op.drop_table("asset_definitions")
