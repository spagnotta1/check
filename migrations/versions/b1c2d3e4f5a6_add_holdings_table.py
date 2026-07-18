"""add holdings table

Revision ID: b1c2d3e4f5a6
Revises: a1b2c3d4e5f6
Create Date: 2026-05-15 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'b1c2d3e4f5a6'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'holdings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('ticker', sa.String(length=20), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('shares', sa.Numeric(14, 6), nullable=False),
        sa.Column('current_value', sa.Numeric(12, 2), nullable=False),
        sa.Column('asset_class', sa.String(length=20), nullable=False, server_default='Stock'),
        sa.Column('account_name', sa.String(length=50), nullable=False, server_default='Brokerage'),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    op.drop_table('holdings')
