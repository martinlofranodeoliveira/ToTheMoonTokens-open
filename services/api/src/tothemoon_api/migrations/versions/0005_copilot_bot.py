"""add copilot proposals and real mode guardrails

Revision ID: 0005_copilot_bot
Revises: 0004_simulated_trade_pnl
Create Date: 2026-05-01 00:00:02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_copilot_bot"
down_revision: str | None = "0004_simulated_trade_pnl"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("organizations") as batch_op:
        batch_op.add_column(
            sa.Column("real_mode_enabled", sa.Boolean(), server_default=sa.false(), nullable=False)
        )
        batch_op.add_column(sa.Column("real_mode_approved_at", sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "real_mode_daily_limit_usd",
                sa.Numeric(18, 6),
                server_default="50.0",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("bot_consecutive_failures", sa.Integer(), server_default="0", nullable=False)
        )

    op.create_table(
        "copilot_proposals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("api_key_id", sa.Integer(), nullable=True),
        sa.Column("token_address", sa.String(length=128), nullable=False),
        sa.Column("chain", sa.String(length=40), nullable=False),
        sa.Column("symbol", sa.String(length=40), nullable=True),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("amount_usd", sa.Numeric(18, 6), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("execution_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"]),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_copilot_proposals_api_key_id"), "copilot_proposals", ["api_key_id"])
    op.create_index(op.f("ix_copilot_proposals_org_id"), "copilot_proposals", ["org_id"])
    op.create_index(op.f("ix_copilot_proposals_status"), "copilot_proposals", ["status"])
    op.create_index(
        op.f("ix_copilot_proposals_token_address"),
        "copilot_proposals",
        ["token_address"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_copilot_proposals_token_address"), table_name="copilot_proposals")
    op.drop_index(op.f("ix_copilot_proposals_status"), table_name="copilot_proposals")
    op.drop_index(op.f("ix_copilot_proposals_org_id"), table_name="copilot_proposals")
    op.drop_index(op.f("ix_copilot_proposals_api_key_id"), table_name="copilot_proposals")
    op.drop_table("copilot_proposals")

    with op.batch_alter_table("organizations") as batch_op:
        batch_op.drop_column("bot_consecutive_failures")
        batch_op.drop_column("real_mode_daily_limit_usd")
        batch_op.drop_column("real_mode_approved_at")
        batch_op.drop_column("real_mode_enabled")
