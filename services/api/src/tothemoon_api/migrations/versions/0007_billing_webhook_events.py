"""add billing webhook ledger and stripe customer ids

Revision ID: 0007_billing_webhook_events
Revises: 0006_observability_audit_context
Create Date: 2026-05-02 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_billing_webhook_events"
down_revision: str | None = "0006_observability_audit_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("subscriptions") as batch_op:
        batch_op.add_column(sa.Column("provider_customer_id", sa.String(length=120), nullable=True))
        batch_op.create_index("ix_subscriptions_provider_customer_id", ["provider_customer_id"])
        batch_op.create_index(
            "ix_subscriptions_provider_subscription_id", ["provider_subscription_id"]
        )

    op.create_table(
        "billing_webhook_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("event_id", sa.String(length=120), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=True),
        sa.Column("received_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
    )
    op.create_index(
        op.f("ix_billing_webhook_events_event_id"), "billing_webhook_events", ["event_id"]
    )
    op.create_index(
        op.f("ix_billing_webhook_events_event_type"), "billing_webhook_events", ["event_type"]
    )
    op.create_index(op.f("ix_billing_webhook_events_org_id"), "billing_webhook_events", ["org_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_billing_webhook_events_org_id"), table_name="billing_webhook_events")
    op.drop_index(op.f("ix_billing_webhook_events_event_type"), table_name="billing_webhook_events")
    op.drop_index(op.f("ix_billing_webhook_events_event_id"), table_name="billing_webhook_events")
    op.drop_table("billing_webhook_events")

    with op.batch_alter_table("subscriptions") as batch_op:
        batch_op.drop_index("ix_subscriptions_provider_subscription_id")
        batch_op.drop_index("ix_subscriptions_provider_customer_id")
        batch_op.drop_column("provider_customer_id")
