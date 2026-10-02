"""Section 16 item 8: customer status page linked from texts.

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('repair_orders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('status_token', sa.String(length=40), nullable=True))
        batch_op.create_index('ix_repair_orders_status_token', ['status_token'], unique=True)


def downgrade() -> None:
    with op.batch_alter_table('repair_orders', schema=None) as batch_op:
        batch_op.drop_index('ix_repair_orders_status_token')
        batch_op.drop_column('status_token')
