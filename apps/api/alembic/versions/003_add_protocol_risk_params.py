"""add protocol_risk_params_snapshots

Revision ID: 003
Revises: 002
Create Date: 2026-03-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "protocol_risk_params_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("protocol", sa.String(64), nullable=False),
        sa.Column("chain", sa.String(64), nullable=False),
        sa.Column("asset", sa.String(32), nullable=False),
        sa.Column("debt_asset", sa.String(32), nullable=True),
        sa.Column("market_address", sa.String(128), nullable=True),
        sa.Column("max_ltv", sa.Float(), nullable=True),
        sa.Column("liquidation_threshold", sa.Float(), nullable=True),
        sa.Column("liquidation_penalty", sa.Float(), nullable=True),
        sa.Column("borrow_cap_native", sa.Float(), nullable=True),
        sa.Column("supply_cap_native", sa.Float(), nullable=True),
        sa.Column("collateral_eligible", sa.Boolean(), nullable=True),
        sa.Column("borrowing_enabled", sa.Boolean(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("available_capacity_native", sa.Float(), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_protocol_risk_params_protocol",
        "protocol_risk_params_snapshots",
        ["protocol"],
    )
    op.create_index(
        "ix_protocol_risk_params_asset",
        "protocol_risk_params_snapshots",
        ["asset"],
    )
    op.create_index(
        "ix_protocol_risk_params_snapshot_at",
        "protocol_risk_params_snapshots",
        ["snapshot_at"],
    )


def downgrade() -> None:
    op.drop_table("protocol_risk_params_snapshots")
