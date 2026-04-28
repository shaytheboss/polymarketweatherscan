"""Initial schema — sport_markets table

Revision ID: 001
Revises:
Create Date: 2025-04-28
"""
from alembic import op
import sqlalchemy as sa

revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'sport_markets',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('condition_id', sa.String(80), nullable=False),
        sa.Column('event_slug', sa.String(200), nullable=True),
        sa.Column('token_id_p1', sa.String(80), nullable=True),
        sa.Column('token_id_p2', sa.String(80), nullable=True),
        sa.Column('player1', sa.String(120), nullable=False),
        sa.Column('player2', sa.String(120), nullable=False),
        sa.Column('sport', sa.String(20), nullable=True, server_default='tennis'),
        sa.Column('poly_price_p1', sa.Float(), nullable=True),
        sa.Column('poly_price_p2', sa.Float(), nullable=True),
        sa.Column('prev_price_p1', sa.Float(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True, server_default='true'),
        sa.Column('last_price_update', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('condition_id'),
    )
    op.create_index('ix_sport_markets_condition_id', 'sport_markets', ['condition_id'])


def downgrade() -> None:
    op.drop_index('ix_sport_markets_condition_id', table_name='sport_markets')
    op.drop_table('sport_markets')
