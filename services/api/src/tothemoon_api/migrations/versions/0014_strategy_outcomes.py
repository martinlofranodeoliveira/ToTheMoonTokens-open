"""add strategy outcome tracking

Revision ID: 0014_strategy_outcomes
Revises: 0013_copilot_scanner_run_status
Create Date: 2026-05-10 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_strategy_outcomes"
down_revision: str | None = "0013_copilot_scanner_run_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "strategy_outcomes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("profile_name", sa.String(length=64), nullable=False),
        sa.Column("family", sa.String(length=16), nullable=True),
        sa.Column("scanner_run_id", sa.Integer(), nullable=True),
        sa.Column("paper_position_id", sa.Integer(), nullable=True),
        sa.Column("candidate_address", sa.String(length=128), nullable=False),
        sa.Column("chain", sa.String(length=40), nullable=False),
        sa.Column("decision", sa.String(length=24), nullable=False),
        sa.Column("entry_price", sa.Numeric(18, 8), nullable=True),
        sa.Column("price_24h", sa.Numeric(18, 8), nullable=True),
        sa.Column("price_7d", sa.Numeric(18, 8), nullable=True),
        sa.Column("paper_pnl_brl", sa.Numeric(18, 8), nullable=True),
        sa.Column("classification", sa.String(length=24), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["paper_position_id"], ["simulated_trades.id"]),
        sa.ForeignKeyConstraint(["scanner_run_id"], ["copilot_scanner_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id",
            "scanner_run_id",
            "candidate_address",
            name="uq_strategy_outcomes_org_run_candidate",
        ),
    )
    op.create_index(op.f("ix_strategy_outcomes_org_id"), "strategy_outcomes", ["org_id"])
    op.create_index(
        op.f("ix_strategy_outcomes_profile_name"),
        "strategy_outcomes",
        ["profile_name"],
    )
    op.create_index(op.f("ix_strategy_outcomes_family"), "strategy_outcomes", ["family"])
    op.create_index(
        op.f("ix_strategy_outcomes_scanner_run_id"),
        "strategy_outcomes",
        ["scanner_run_id"],
    )
    op.create_index(
        op.f("ix_strategy_outcomes_paper_position_id"),
        "strategy_outcomes",
        ["paper_position_id"],
    )
    op.create_index(
        op.f("ix_strategy_outcomes_candidate_address"),
        "strategy_outcomes",
        ["candidate_address"],
    )
    op.create_index(
        op.f("ix_strategy_outcomes_decision"),
        "strategy_outcomes",
        ["decision"],
    )
    op.create_index(
        op.f("ix_strategy_outcomes_classification"),
        "strategy_outcomes",
        ["classification"],
    )
    op.create_index(
        "ix_strategy_outcomes_org_created",
        "strategy_outcomes",
        ["org_id", "created_at"],
    )
    op.create_index(
        "ix_strategy_outcomes_org_profile_created",
        "strategy_outcomes",
        ["org_id", "profile_name", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_strategy_outcomes_org_profile_created", table_name="strategy_outcomes")
    op.drop_index("ix_strategy_outcomes_org_created", table_name="strategy_outcomes")
    op.drop_index(op.f("ix_strategy_outcomes_classification"), table_name="strategy_outcomes")
    op.drop_index(op.f("ix_strategy_outcomes_decision"), table_name="strategy_outcomes")
    op.drop_index(op.f("ix_strategy_outcomes_candidate_address"), table_name="strategy_outcomes")
    op.drop_index(op.f("ix_strategy_outcomes_paper_position_id"), table_name="strategy_outcomes")
    op.drop_index(op.f("ix_strategy_outcomes_scanner_run_id"), table_name="strategy_outcomes")
    op.drop_index(op.f("ix_strategy_outcomes_family"), table_name="strategy_outcomes")
    op.drop_index(op.f("ix_strategy_outcomes_profile_name"), table_name="strategy_outcomes")
    op.drop_index(op.f("ix_strategy_outcomes_org_id"), table_name="strategy_outcomes")
    op.drop_table("strategy_outcomes")
