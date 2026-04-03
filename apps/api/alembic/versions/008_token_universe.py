"""add token_universe table

Revision ID: 008
Revises: 007
Create Date: 2026-04-02

Stores the merged token universe (static ASSET_REGISTRY + CoinGecko top 500)
so the API can serve paginated, searchable token lists backed by DB queries.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "token_universe",
        sa.Column("canonical_id", sa.String(64), nullable=False),
        sa.Column("coingecko_id", sa.String(128), nullable=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("symbol", sa.String(64), nullable=False),
        sa.Column("umbrella", sa.String(32), nullable=False),
        sa.Column("sub_type", sa.String(64), nullable=False),
        sa.Column("market_cap_rank", sa.Integer(), nullable=True),
        sa.Column("market_cap_usd", sa.Float(), nullable=True),
        sa.Column("current_price_usd", sa.Float(), nullable=True),
        sa.Column("price_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("chains", JSONB(), nullable=False, server_default="[]"),
        sa.Column("is_static", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=False),
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
    )
    op.create_index("ix_tu_symbol", "token_universe", ["symbol"])
    op.create_index("ix_tu_market_cap_rank", "token_universe", ["market_cap_rank"])
    op.create_index("ix_tu_umbrella", "token_universe", ["umbrella"])
    op.create_index("ix_tu_is_static", "token_universe", ["is_static"])
    # Non-unique index on coingecko_id — uniqueness is enforced at application level
    op.create_index("ix_tu_coingecko_id", "token_universe", ["coingecko_id"])


def downgrade() -> None:
    op.drop_table("token_universe")
