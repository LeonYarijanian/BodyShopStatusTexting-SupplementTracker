"""Section 16 item 5: photo updates by picture message (MMS).

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.add_column(sa.Column('media_token', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('media_content_type', sa.String(length=32), nullable=True))
        batch_op.create_index('ix_messages_media_token', ['media_token'], unique=True)


def downgrade() -> None:
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.drop_index('ix_messages_media_token')
        batch_op.drop_column('media_content_type')
        batch_op.drop_column('media_token')
