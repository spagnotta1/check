"""add anomaly_score to transactions and budgets table

Revision ID: a1b2c3d4e5f6
Revises: 2f47d531606f
Create Date: 2026-05-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a1b2c3d4e5f6'
down_revision = '2f47d531606f'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('transactions', sa.Column('anomaly_score', sa.Float(), nullable=True))

    op.create_table(
        'budgets',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('category', sa.String(length=50), nullable=False),
        sa.Column('account_name', sa.String(length=50), nullable=False, server_default='both'),
        sa.Column('monthly_limit', sa.Numeric(10, 2), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_budget_unique', 'budgets', ['category', 'account_name'], unique=True)


def downgrade():
    op.drop_index('idx_budget_unique', table_name='budgets')
    op.drop_table('budgets')
    op.drop_column('transactions', 'anomaly_score')
