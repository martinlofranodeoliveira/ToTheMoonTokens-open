"""add billing and payment api key scopes

Revision ID: 0010_billing_payment_api_key_scopes
Revises: 0009_token_discovery_history
Create Date: 2026-05-07 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_billing_payment_api_key_scopes"
down_revision: str | None = "0009_token_discovery_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_SCOPES = "copilot:proposals simulate:order tokens:audit"


def upgrade() -> None:
    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.alter_column(
            "scopes",
            existing_type=sa.String(length=255),
            server_default=DEFAULT_SCOPES,
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.alter_column(
            "scopes",
            existing_type=sa.String(length=255),
            server_default=None,
            existing_nullable=False,
        )
