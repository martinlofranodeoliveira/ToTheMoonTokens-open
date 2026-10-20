"""track copilot scanner run failures

Revision ID: 0013_copilot_scanner_run_status
Revises: 0012_link_copilot_proposals_to_scanner_runs
Create Date: 2026-05-08 06:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_copilot_scanner_run_status"
down_revision: str | None = "0012_link_copilot_proposals_to_scanner_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("copilot_scanner_runs") as batch_op:
        batch_op.add_column(
            sa.Column("status", sa.String(length=20), nullable=False, server_default="completed")
        )
        batch_op.add_column(sa.Column("error_summary", sa.Text(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_copilot_scanner_runs_status"),
            ["status"],
            unique=False,
        )
    with op.batch_alter_table("copilot_scanner_runs") as batch_op:
        batch_op.alter_column("status", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("copilot_scanner_runs") as batch_op:
        batch_op.drop_index(batch_op.f("ix_copilot_scanner_runs_status"))
        batch_op.drop_column("error_summary")
        batch_op.drop_column("status")
