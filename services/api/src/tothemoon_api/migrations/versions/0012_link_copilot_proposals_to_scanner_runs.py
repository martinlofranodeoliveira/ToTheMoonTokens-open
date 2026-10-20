"""link copilot proposals to scanner runs

Revision ID: 0012_link_copilot_proposals_to_scanner_runs
Revises: 0011_copilot_scanner_runs
Create Date: 2026-05-08 00:00:01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_link_copilot_proposals_to_scanner_runs"
down_revision: str | None = "0011_copilot_scanner_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("copilot_proposals") as batch_op:
        batch_op.add_column(sa.Column("scanner_run_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_copilot_proposals_scanner_run_id_copilot_scanner_runs",
            "copilot_scanner_runs",
            ["scanner_run_id"],
            ["id"],
        )
        batch_op.create_index(
            batch_op.f("ix_copilot_proposals_scanner_run_id"),
            ["scanner_run_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("copilot_proposals") as batch_op:
        batch_op.drop_index(batch_op.f("ix_copilot_proposals_scanner_run_id"))
        batch_op.drop_constraint(
            "fk_copilot_proposals_scanner_run_id_copilot_scanner_runs",
            type_="foreignkey",
        )
        batch_op.drop_column("scanner_run_id")
