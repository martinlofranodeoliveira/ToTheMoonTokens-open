"""add per-organization provider credentials

Revision ID: 0008_org_provider_credentials
Revises: 0007_billing_webhook_events
Create Date: 2026-05-04 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_org_provider_credentials"
down_revision: str | None = "0007_billing_webhook_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "org_provider_credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False),
        sa.Column("key_hint", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "provider", name="uq_org_provider_credentials_org_provider"),
    )
    op.create_index(
        op.f("ix_org_provider_credentials_org_id"),
        "org_provider_credentials",
        ["org_id"],
    )
    op.create_index(
        op.f("ix_org_provider_credentials_provider"),
        "org_provider_credentials",
        ["provider"],
    )
    op.create_index(
        "ix_org_provider_credentials_org_provider",
        "org_provider_credentials",
        ["org_id", "provider"],
    )


def downgrade() -> None:
    op.drop_index("ix_org_provider_credentials_org_provider", table_name="org_provider_credentials")
    op.drop_index(op.f("ix_org_provider_credentials_provider"), table_name="org_provider_credentials")
    op.drop_index(op.f("ix_org_provider_credentials_org_id"), table_name="org_provider_credentials")
    op.drop_table("org_provider_credentials")
