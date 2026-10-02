"""Section 16 item 3: daily email digest of follow-ups due.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('shop_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('digest_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('digest_recipients', sa.JSON(), nullable=False, server_default='[]'))
        batch_op.add_column(sa.Column('digest_send_time', sa.Time(), nullable=False, server_default='07:30:00'))
        batch_op.add_column(sa.Column('digest_last_sent_on', sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('shop_settings', schema=None) as batch_op:
        batch_op.drop_column('digest_last_sent_on')
        batch_op.drop_column('digest_send_time')
        batch_op.drop_column('digest_recipients')
        batch_op.drop_column('digest_enabled')
