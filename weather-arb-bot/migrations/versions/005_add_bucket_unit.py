"""Add bucket_unit column to market_outcomes

Revision ID: 005
Revises: 001
Create Date: 2026-05-26 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "005"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "market_outcomes",
        sa.Column("bucket_unit", sa.String(1), nullable=False, server_default="F"),
    )
    # Backfill: any outcome whose label contains °C stores native Celsius integers
    op.execute(
        "UPDATE market_outcomes SET bucket_unit = 'C' WHERE bucket_label LIKE '%°C%'"
    )


def downgrade() -> None:
    op.drop_column("market_outcomes", "bucket_unit")
