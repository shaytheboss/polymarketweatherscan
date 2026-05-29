"""Add forecast_accuracy table for B1/B2 statistical calibration

Revision ID: 006
Revises: 005
Create Date: 2026-05-29 00:00:00.000000

This table stores per-(city, source, lead_time_days, month) accuracy metrics.
Populated by a post-resolution job. Used to supply learned sigma and bias
correction values to probability_estimator once sufficient samples accumulate.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "forecast_accuracy",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("city_id", sa.Integer(), sa.ForeignKey("cities.id"), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("lead_time_days", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mae", sa.Float(), nullable=True),
        sa.Column("bias", sa.Float(), nullable=True),
        sa.Column("sigma_estimate", sa.Float(), nullable=True),
        sa.Column(
            "last_updated",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "city_id", "source", "lead_time_days", "month",
            name="uq_forecast_accuracy",
        ),
    )


def downgrade() -> None:
    op.drop_table("forecast_accuracy")
