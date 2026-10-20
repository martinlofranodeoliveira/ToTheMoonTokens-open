"""add audit log organization and before/after context

Revision ID: 0006_observability_audit_context
Revises: 0005_copilot_bot
Create Date: 2026-05-01 00:00:03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_observability_audit_context"
down_revision: str | None = "0005_copilot_bot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.add_column(sa.Column("org_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("before", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("after", sa.JSON(), nullable=True))
        batch_op.create_foreign_key(
            "fk_audit_logs_org_id_organizations",
            "organizations",
            ["org_id"],
            ["id"],
        )
        batch_op.create_index("ix_audit_logs_org_id", ["org_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.drop_index("ix_audit_logs_org_id")
        batch_op.drop_constraint("fk_audit_logs_org_id_organizations", type_="foreignkey")
        batch_op.drop_column("after")
        batch_op.drop_column("before")
        batch_op.drop_column("org_id")
