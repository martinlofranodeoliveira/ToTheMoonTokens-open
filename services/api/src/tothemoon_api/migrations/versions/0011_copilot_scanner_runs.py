"""add copilot scanner run summaries

Revision ID: 0011_copilot_scanner_runs
Revises: 0010_billing_payment_api_key_scopes
Create Date: 2026-05-08 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_copilot_scanner_runs"
down_revision: str | None = "0010_billing_payment_api_key_scopes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "copilot_scanner_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("api_key_id", sa.Integer(), nullable=True),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("query", sa.String(length=80), nullable=False),
        sa.Column("execution_mode", sa.String(length=20), nullable=False),
        sa.Column("thresholds_json", sa.JSON(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("blocked_count", sa.Integer(), nullable=False),
        sa.Column("queued_count", sa.Integer(), nullable=False),
        sa.Column("paper_executed_count", sa.Integer(), nullable=False),
        sa.Column("provider_readiness_summary", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"]),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_copilot_scanner_runs_actor_user_id"),
        "copilot_scanner_runs",
        ["actor_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_copilot_scanner_runs_api_key_id"),
        "copilot_scanner_runs",
        ["api_key_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_copilot_scanner_runs_execution_mode"),
        "copilot_scanner_runs",
        ["execution_mode"],
        unique=False,
    )
    op.create_index(
        op.f("ix_copilot_scanner_runs_org_id"),
        "copilot_scanner_runs",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        "ix_copilot_scanner_runs_org_created",
        "copilot_scanner_runs",
        ["org_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_copilot_scanner_runs_org_created", table_name="copilot_scanner_runs")
    op.drop_index(op.f("ix_copilot_scanner_runs_org_id"), table_name="copilot_scanner_runs")
    op.drop_index(
        op.f("ix_copilot_scanner_runs_execution_mode"),
        table_name="copilot_scanner_runs",
    )
    op.drop_index(op.f("ix_copilot_scanner_runs_api_key_id"), table_name="copilot_scanner_runs")
    op.drop_index(
        op.f("ix_copilot_scanner_runs_actor_user_id"),
        table_name="copilot_scanner_runs",
    )
    op.drop_table("copilot_scanner_runs")
