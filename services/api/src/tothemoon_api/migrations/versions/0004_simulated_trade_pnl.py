"""add simulated trade close price and realized pnl

Revision ID: 0004_simulated_trade_pnl
Revises: 0003_multi_tenancy_quota
Create Date: 2026-05-01 00:00:01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_simulated_trade_pnl"
down_revision: str | None = "0003_multi_tenancy_quota"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("simulated_trades") as batch_op:
        batch_op.add_column(sa.Column("exit_price", sa.Numeric(18, 8), nullable=True))
        batch_op.add_column(sa.Column("realized_pnl_usd", sa.Numeric(18, 8), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("simulated_trades") as batch_op:
        batch_op.drop_column("realized_pnl_usd")
        batch_op.drop_column("exit_price")
