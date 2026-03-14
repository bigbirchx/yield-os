"""add staking_snapshots and lending columns

Revision ID: 002
Revises: 001
Create Date: 2026-03-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- Extend lending_market_snapshots -----------------------------------
    op.add_column(
        "lending_market_snapshots",
        sa.Column("chain", sa.String(64), nullable=True),
    )
    op.add_column(
        "lending_market_snapshots",
        sa.Column("pool_id", sa.String(128), nullable=True),
    )
    op.add_column(
        "lending_market_snapshots",
        sa.Column("reward_supply_apy", sa.Float(), nullable=True),
    )
    op.add_column(
        "lending_market_snapshots",
        sa.Column("reward_borrow_apy", sa.Float(), nullable=True),
    )
    op.create_index(
        "ix_lending_market_snapshots_pool_id",
        "lending_market_snapshots",
        ["pool_id"],
    )

    # -- Create staking_snapshots ------------------------------------------
    op.create_table(
        "staking_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("underlying_symbol", sa.String(32), nullable=False),
        sa.Column("protocol", sa.String(64), nullable=False),
        sa.Column("chain", sa.String(64), nullable=False),
        sa.Column("pool_id", sa.String(128), nullable=True),
        sa.Column("staking_apy", sa.Float(), nullable=True),
        sa.Column("base_apy", sa.Float(), nullable=True),
        sa.Column("reward_apy", sa.Float(), nullable=True),
        sa.Column("tvl_usd", sa.Float(), nullable=True),
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
    op.create_index("ix_staking_snapshots_symbol", "staking_snapshots", ["symbol"])
    op.create_index(
        "ix_staking_snapshots_snapshot_at", "staking_snapshots", ["snapshot_at"]
    )
    op.create_index(
        "ix_staking_snapshots_pool_id", "staking_snapshots", ["pool_id"]
    )


def downgrade() -> None:
    op.drop_table("staking_snapshots")
    op.drop_index(
        "ix_lending_market_snapshots_pool_id", table_name="lending_market_snapshots"
    )
    op.drop_column("lending_market_snapshots", "reward_borrow_apy")
    op.drop_column("lending_market_snapshots", "reward_supply_apy")
    op.drop_column("lending_market_snapshots", "pool_id")
    op.drop_column("lending_market_snapshots", "chain")
