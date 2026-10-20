"""add api key organization scope and resource usage

Revision ID: 0003_multi_tenancy_quota
Revises: 0002_add_orgs
Create Date: 2026-05-01 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_multi_tenancy_quota"
down_revision: str | None = "0002_add_orgs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.add_column(sa.Column("org_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_api_keys_org_id_organizations", "organizations", ["org_id"], ["id"]
        )
        batch_op.create_index("ix_api_keys_org_id", ["org_id"], unique=False)

    with op.batch_alter_table("usage_records") as batch_op:
        batch_op.add_column(
            sa.Column(
                "resource",
                sa.String(length=40),
                server_default="simulate_order",
                nullable=False,
            )
        )
        batch_op.drop_constraint("uq_usage_records_org_day", type_="unique")
        batch_op.create_unique_constraint(
            "uq_usage_records_org_day_resource",
            ["org_id", "day", "resource"],
        )


def downgrade() -> None:
    with op.batch_alter_table("usage_records") as batch_op:
        batch_op.drop_constraint("uq_usage_records_org_day_resource", type_="unique")
        batch_op.create_unique_constraint("uq_usage_records_org_day", ["org_id", "day"])
        batch_op.drop_column("resource")

    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.drop_index("ix_api_keys_org_id")
        batch_op.drop_constraint("fk_api_keys_org_id_organizations", type_="foreignkey")
        batch_op.drop_column("org_id")
