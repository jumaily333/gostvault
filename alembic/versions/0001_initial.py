"""create wallet_analyses table

Revision ID: 0001_initial
Revises:
Create Date: 2025-01-01 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enums
    chain_enum = sa.Enum(
        "bitcoin", "ethereum", "solana",
        name="chain_enum",
    )
    risk_level_enum = sa.Enum(
        "LOW_ACTIVITY", "MODERATE_ACTIVITY", "HIGH_ACTIVITY", "UNKNOWN",
        name="risk_level_enum",
    )
    wallet_type_enum = sa.Enum(
        "POSSIBLE_COLD_STORAGE",
        "LIKELY_HOT_WALLET",
        "EXCHANGE_WALLET",
        "WHALE_WALLET",
        "FRESH_WALLET",
        "UNKNOWN",
        name="wallet_type_enum",
    )
    chain_enum.create(op.get_bind(), checkfirst=True)
    risk_level_enum.create(op.get_bind(), checkfirst=True)
    wallet_type_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "wallet_analyses",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("chain", chain_enum, nullable=False),
        sa.Column("address", sa.String(128), nullable=False),
        sa.Column("wallet_age_days", sa.Integer(), nullable=True),
        sa.Column("days_since_last_activity", sa.Integer(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("transaction_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("avg_tx_interval_days", sa.Float(), nullable=True),
        sa.Column("incoming_tx_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("outgoing_tx_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("dormancy_score", sa.Integer(), nullable=False),
        sa.Column("cold_storage_probability", sa.Integer(), nullable=False),
        sa.Column("risk_level", risk_level_enum, nullable=False),
        sa.Column("wallet_type_estimate", wallet_type_enum, nullable=False),
        sa.Column("raw_metrics", JSONB, nullable=False, server_default="{}"),
        sa.Column("analysis_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # Indexes
    op.create_index("ix_wallet_analyses_chain", "wallet_analyses", ["chain"])
    op.create_index("ix_wallet_analyses_address", "wallet_analyses", ["address"])
    op.create_index(
        "ix_wallet_analyses_chain_address",
        "wallet_analyses",
        ["chain", "address"],
        unique=False,
    )
    op.create_index(
        "ix_wallet_analyses_dormancy_score",
        "wallet_analyses",
        ["dormancy_score"],
    )


def downgrade() -> None:
    op.drop_table("wallet_analyses")
    sa.Enum(name="chain_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="risk_level_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="wallet_type_enum").drop(op.get_bind(), checkfirst=True)
