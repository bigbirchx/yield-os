"""book tables for portfolio overlay

Revision ID: 009
Revises: 008
Create Date: 2026-04-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── books ──────────────────────────────────────────────────────
    op.create_table(
        "books",
        sa.Column("book_id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("source_file", sa.String(512), nullable=False),
        sa.Column("import_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("as_of_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_positions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("summary", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # ── book_positions ─────────────────────────────────────────────
    op.create_table(
        "book_positions",
        sa.Column("book_id", sa.String(64), nullable=False),
        sa.Column("loan_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("counterparty_name", sa.String(256), nullable=False),
        sa.Column("counterparty_legal_entity", sa.String(256), nullable=True),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("principal_asset", sa.String(64), nullable=False),
        sa.Column("principal_qty", sa.Float(), nullable=False),
        sa.Column("principal_usd", sa.Float(), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("maturity_date", sa.Date(), nullable=True),
        sa.Column("tenor", sa.String(16), nullable=False),
        sa.Column("recall_period_days", sa.Float(), nullable=True),
        sa.Column("collateral_assets_raw", sa.Text(), nullable=True),
        sa.Column("initial_collateralization_ratio_pct", sa.Float(), nullable=True),
        sa.Column(
            "rehypothecation_allowed",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "collateral_substitution_allowed",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "is_collateralized",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column("loan_type", sa.String(32), nullable=False),
        sa.Column("interest_rate_pct", sa.Float(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("query_notes", sa.Text(), nullable=True),
        sa.Column("protocol_name", sa.String(128), nullable=True),
        sa.Column("protocol_chain", sa.String(32), nullable=True),
        sa.Column("umbrella_group", sa.String(32), nullable=True),
        sa.Column("matched_opportunity_id", sa.String(512), nullable=True),
        sa.Column("current_market_rate_pct", sa.Float(), nullable=True),
        sa.Column("rate_vs_market_bps", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("book_id", "loan_id"),
        sa.ForeignKeyConstraint(
            ["book_id"], ["books.book_id"], ondelete="CASCADE"
        ),
    )
    op.create_index("ix_bp_customer_id", "book_positions", ["customer_id"])
    op.create_index("ix_bp_category", "book_positions", ["category"])
    op.create_index("ix_bp_direction", "book_positions", ["direction"])
    op.create_index("ix_bp_principal_asset", "book_positions", ["principal_asset"])
    op.create_index("ix_bp_loan_type", "book_positions", ["loan_type"])
    op.create_index("ix_bp_status", "book_positions", ["status"])

    # ── book_observed_collateral ───────────────────────────────────
    op.create_table(
        "book_observed_collateral",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("book_id", sa.String(64), nullable=False),
        sa.Column("asof_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("counterparty_name", sa.String(256), nullable=False),
        sa.Column("collateral_relationship", sa.String(64), nullable=False),
        sa.Column("collateral_asset", sa.String(64), nullable=False),
        sa.Column("units_posted", sa.Float(), nullable=False),
        sa.Column("data_source", sa.String(128), nullable=False),
        sa.Column(
            "is_tri_party",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column("custodial_venue", sa.String(128), nullable=False),
        sa.ForeignKeyConstraint(
            ["book_id"], ["books.book_id"], ondelete="CASCADE"
        ),
    )
    op.create_index("ix_boc_book_id", "book_observed_collateral", ["book_id"])
    op.create_index(
        "ix_boc_customer_id", "book_observed_collateral", ["customer_id"]
    )

    # ── book_collateral_allocations ────────────────────────────────
    op.create_table(
        "book_collateral_allocations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("book_id", sa.String(64), nullable=False),
        sa.Column("loan_id", sa.Integer(), nullable=False),
        sa.Column("collateral_asset", sa.String(64), nullable=False),
        sa.Column("allocated_units", sa.Float(), nullable=False),
        sa.Column("allocated_usd", sa.Float(), nullable=False),
        sa.Column("allocation_weight_pct", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["book_id"], ["books.book_id"], ondelete="CASCADE"
        ),
    )
    op.create_index("ix_bca_book_id", "book_collateral_allocations", ["book_id"])
    op.create_index("ix_bca_loan_id", "book_collateral_allocations", ["loan_id"])


def downgrade() -> None:
    op.drop_table("book_collateral_allocations")
    op.drop_table("book_observed_collateral")
    op.drop_table("book_positions")
    op.drop_table("books")
