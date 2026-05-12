"""Paper trades

Revision ID: 002
Revises: 001
Create Date: 2026-05-12 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "paper_trades",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "opportunity_id",
            sa.Integer(),
            sa.ForeignKey("opportunities.id"),
            nullable=False,
        ),
        sa.Column(
            "outcome_id",
            sa.Integer(),
            sa.ForeignKey("market_outcomes.id"),
            nullable=False,
        ),
        sa.Column(
            "market_id", sa.Integer(), sa.ForeignKey("markets.id"), nullable=False
        ),
        sa.Column("city_id", sa.Integer(), sa.ForeignKey("cities.id")),
        sa.Column("side", sa.String(3), nullable=False),
        sa.Column("entry_price", sa.Numeric(6, 4), nullable=False),
        sa.Column("estimated_true_prob", sa.Numeric(6, 4), nullable=False),
        sa.Column("edge_at_entry", sa.Numeric(6, 4), nullable=False),
        sa.Column("confidence_at_entry", sa.Integer(), nullable=False),
        sa.Column("size_usd", sa.Numeric(10, 2), nullable=False),
        sa.Column(
            "opened_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.Column("closed_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("exit_price", sa.Numeric(6, 4)),
        sa.Column("resolution_outcome", sa.String(20)),
        sa.Column("pnl_usd", sa.Numeric(12, 2)),
        sa.Column(
            "settled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("notes", sa.Text()),
        sa.Column("signals_snapshot", postgresql.JSONB()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("opportunity_id", name="uq_paper_trade_opportunity"),
    )
    op.create_index(
        "idx_paper_trades_outcome", "paper_trades", ["outcome_id", "opened_at"]
    )
    op.create_index("idx_paper_trades_open", "paper_trades", ["settled"])


def downgrade() -> None:
    op.drop_index("idx_paper_trades_open", "paper_trades")
    op.drop_index("idx_paper_trades_outcome", "paper_trades")
    op.drop_table("paper_trades")
