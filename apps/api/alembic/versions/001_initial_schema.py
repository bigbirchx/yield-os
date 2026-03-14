"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-03-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("family", sa.String(32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol"),
    )
    op.create_index("ix_assets_symbol", "assets", ["symbol"])

    op.create_table(
        "derivatives_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("venue", sa.String(64), nullable=False),
        sa.Column("funding_rate", sa.Float(), nullable=True),
        sa.Column("open_interest_usd", sa.Float(), nullable=True),
        sa.Column("basis_annualized", sa.Float(), nullable=True),
        sa.Column("mark_price", sa.Float(), nullable=True),
        sa.Column("index_price", sa.Float(), nullable=True),
        sa.Column("spot_volume_usd", sa.Float(), nullable=True),
        sa.Column("perp_volume_usd", sa.Float(), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_derivatives_snapshots_symbol", "derivatives_snapshots", ["symbol"])
    op.create_index(
        "ix_derivatives_snapshots_snapshot_at", "derivatives_snapshots", ["snapshot_at"]
    )

    op.create_table(
        "lending_market_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("protocol", sa.String(64), nullable=False),
        sa.Column("market", sa.String(128), nullable=False),
        sa.Column("supply_apy", sa.Float(), nullable=True),
        sa.Column("borrow_apy", sa.Float(), nullable=True),
        sa.Column("utilization", sa.Float(), nullable=True),
        sa.Column("tvl_usd", sa.Float(), nullable=True),
        sa.Column("available_liquidity_usd", sa.Float(), nullable=True),
        sa.Column("borrow_cap_usd", sa.Float(), nullable=True),
        sa.Column("supply_cap_usd", sa.Float(), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lending_market_snapshots_symbol", "lending_market_snapshots", ["symbol"])
    op.create_index(
        "ix_lending_market_snapshots_snapshot_at",
        "lending_market_snapshots",
        ["snapshot_at"],
    )


def downgrade() -> None:
    op.drop_table("lending_market_snapshots")
    op.drop_table("derivatives_snapshots")
    op.drop_table("assets")
