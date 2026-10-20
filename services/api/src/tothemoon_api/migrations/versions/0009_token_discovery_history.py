"""add account scoped token discovery history

Revision ID: 0009_token_discovery_history
Revises: 0008_org_provider_credentials
Create Date: 2026-05-05 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_token_discovery_history"
down_revision: str | None = "0008_org_provider_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "token_discovery_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("token_address", sa.String(length=128), nullable=False),
        sa.Column("chain", sa.String(length=40), nullable=False),
        sa.Column("symbol", sa.String(length=40), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("recommendation", sa.String(length=40), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_token_discovery_history_chain"),
        "token_discovery_history",
        ["chain"],
        unique=False,
    )
    op.create_index(
        op.f("ix_token_discovery_history_org_id"),
        "token_discovery_history",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        "ix_token_discovery_history_org_created",
        "token_discovery_history",
        ["org_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_token_discovery_history_token_address"),
        "token_discovery_history",
        ["token_address"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_token_discovery_history_token_address"),
        table_name="token_discovery_history",
    )
    op.drop_index("ix_token_discovery_history_org_created", table_name="token_discovery_history")
    op.drop_index(op.f("ix_token_discovery_history_org_id"), table_name="token_discovery_history")
    op.drop_index(op.f("ix_token_discovery_history_chain"), table_name="token_discovery_history")
    op.drop_table("token_discovery_history")
