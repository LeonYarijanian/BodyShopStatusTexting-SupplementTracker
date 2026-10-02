"""Section 16 item 7: AI-drafted adjuster follow-up emails that staff approve.

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'adjuster_emails',
        sa.Column('shop_id', sa.Integer(), nullable=False),
        sa.Column('supplement_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.Enum('DRAFT', 'SENT', 'DISCARDED', 'FAILED', name='adjusteremailstatus', native_enum=False, length=32), nullable=False),
        sa.Column('source', sa.Enum('AI', 'TEMPLATE', name='draftsource', native_enum=False, length=32), nullable=False),
        sa.Column('model', sa.String(length=64), nullable=True),
        sa.Column('to_email', sa.String(length=254), nullable=True),
        sa.Column('subject', sa.String(length=200), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('created_by_user_id', sa.Integer(), nullable=False),
        sa.Column('sent_by_user_id', sa.Integer(), nullable=True),
        sa.Column('sent_at', sa.DateTime(), nullable=True),
        sa.Column('error_text', sa.String(length=200), nullable=True),
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['shop_id'], ['shops.id']),
        sa.ForeignKeyConstraint(['supplement_id'], ['supplements.id']),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['sent_by_user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('adjuster_emails', schema=None) as batch_op:
        batch_op.create_index('ix_adjuster_emails_shop_id', ['shop_id'], unique=False)
        batch_op.create_index('ix_adjuster_emails_supplement_id', ['supplement_id'], unique=False)


def downgrade() -> None:
    op.drop_table('adjuster_emails')
