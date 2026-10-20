"""add organizations, billing, usage, and audit tables

Revision ID: 0002_add_orgs
Revises: 0001_initial
Create Date: 2026-04-30 00:00:01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_add_orgs"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("monthly_request_limit", sa.Integer(), nullable=False),
        sa.Column("monthly_token_audit_limit", sa.Integer(), nullable=False),
        sa.Column("active_api_key_limit", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index(op.f("ix_plans_code"), "plans", ["code"], unique=True)

    plans = sa.table(
        "plans",
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("monthly_request_limit", sa.Integer),
        sa.column("monthly_token_audit_limit", sa.Integer),
        sa.column("active_api_key_limit", sa.Integer),
    )
    op.bulk_insert(
        plans,
        [
            {
                "code": "free",
                "name": "Free",
                "monthly_request_limit": 1000,
                "monthly_token_audit_limit": 100,
                "active_api_key_limit": 1,
            },
            {
                "code": "pro",
                "name": "Pro",
                "monthly_request_limit": 100000,
                "monthly_token_audit_limit": 10000,
                "active_api_key_limit": 10,
            },
            {
                "code": "enterprise",
                "name": "Enterprise",
                "monthly_request_limit": 1000000,
                "monthly_token_audit_limit": 100000,
                "active_api_key_limit": 100,
            },
        ],
    )

    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_organizations_plan_id"), "organizations", ["plan_id"], unique=False)

    op.create_table(
        "memberships",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "org_id", name="uq_memberships_user_org"),
    )
    op.create_index(op.f("ix_memberships_org_id"), "memberships", ["org_id"], unique=False)
    op.create_index(op.f("ix_memberships_user_id"), "memberships", ["user_id"], unique=False)

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("provider_subscription_id", sa.String(length=120), nullable=True),
        sa.Column("current_period_end", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_subscriptions_org_id"), "subscriptions", ["org_id"], unique=False)

    op.create_table(
        "usage_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False),
        sa.Column("simulated_volume_usd", sa.Numeric(18, 6), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "day", name="uq_usage_records_org_day"),
    )
    op.create_index(op.f("ix_usage_records_day"), "usage_records", ["day"], unique=False)
    op.create_index(op.f("ix_usage_records_org_id"), "usage_records", ["org_id"], unique=False)

    op.create_table(
        "simulated_trades",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("api_key_id", sa.Integer(), nullable=True),
        sa.Column("token_address", sa.String(length=128), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("amount", sa.Numeric(18, 6), nullable=False),
        sa.Column("entry_price", sa.Numeric(18, 8), nullable=False),
        sa.Column("fees_total", sa.Numeric(18, 8), nullable=False),
        sa.Column("slippage_bps", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"]),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_simulated_trades_api_key_id"), "simulated_trades", ["api_key_id"], unique=False
    )
    op.create_index(
        op.f("ix_simulated_trades_org_id"), "simulated_trades", ["org_id"], unique=False
    )
    op.create_index(
        op.f("ix_simulated_trades_status"), "simulated_trades", ["status"], unique=False
    )
    op.create_index(
        op.f("ix_simulated_trades_token_address"),
        "simulated_trades",
        ["token_address"],
        unique=False,
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("actor_type", sa.String(length=40), nullable=False),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("target_type", sa.String(length=80), nullable=False),
        sa.Column("target_id", sa.String(length=120), nullable=True),
        sa.Column("ip", sa.String(length=80), nullable=True),
        sa.Column("ua", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_logs_action"), "audit_logs", ["action"], unique=False)
    op.create_index(op.f("ix_audit_logs_actor_id"), "audit_logs", ["actor_id"], unique=False)

    op.create_table(
        "nanopayment_receipts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("resource_id", sa.String(length=120), nullable=False),
        sa.Column("amount_usd", sa.Numeric(18, 6), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("tx_hash", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_nanopayment_receipts_org_id"), "nanopayment_receipts", ["org_id"], unique=False
    )
    op.create_index(
        op.f("ix_nanopayment_receipts_resource_id"),
        "nanopayment_receipts",
        ["resource_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_nanopayment_receipts_status"), "nanopayment_receipts", ["status"], unique=False
    )
    op.create_index(
        op.f("ix_nanopayment_receipts_tx_hash"), "nanopayment_receipts", ["tx_hash"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_nanopayment_receipts_tx_hash"), table_name="nanopayment_receipts")
    op.drop_index(op.f("ix_nanopayment_receipts_status"), table_name="nanopayment_receipts")
    op.drop_index(op.f("ix_nanopayment_receipts_resource_id"), table_name="nanopayment_receipts")
    op.drop_index(op.f("ix_nanopayment_receipts_org_id"), table_name="nanopayment_receipts")
    op.drop_table("nanopayment_receipts")
    op.drop_index(op.f("ix_audit_logs_actor_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_action"), table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index(op.f("ix_simulated_trades_token_address"), table_name="simulated_trades")
    op.drop_index(op.f("ix_simulated_trades_status"), table_name="simulated_trades")
    op.drop_index(op.f("ix_simulated_trades_org_id"), table_name="simulated_trades")
    op.drop_index(op.f("ix_simulated_trades_api_key_id"), table_name="simulated_trades")
    op.drop_table("simulated_trades")
    op.drop_index(op.f("ix_usage_records_org_id"), table_name="usage_records")
    op.drop_index(op.f("ix_usage_records_day"), table_name="usage_records")
    op.drop_table("usage_records")
    op.drop_index(op.f("ix_subscriptions_org_id"), table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_index(op.f("ix_memberships_user_id"), table_name="memberships")
    op.drop_index(op.f("ix_memberships_org_id"), table_name="memberships")
    op.drop_table("memberships")
    op.drop_index(op.f("ix_organizations_plan_id"), table_name="organizations")
    op.drop_table("organizations")
    op.drop_index(op.f("ix_plans_code"), table_name="plans")
    op.drop_table("plans")
