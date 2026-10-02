"""Section 16 item 6: review requests after delivery, with their own consent.

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None

DEFAULT_TEMPLATE = (
    "{shop_name}: Thanks again for trusting us with your {vehicle}, {first_name}. "
    "If you have a minute, a review helps us a lot: {review_url} Reply STOP to opt out."
)


def upgrade() -> None:
    with op.batch_alter_table('consents', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'purpose',
                sa.Enum('REPAIR_UPDATES', 'REVIEW_REQUESTS', name='consentpurpose', native_enum=False, length=32),
                nullable=False,
                server_default='REPAIR_UPDATES',
            )
        )
    with op.batch_alter_table('shop_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('review_request_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('review_request_delay_days', sa.Integer(), nullable=False, server_default='2'))
        batch_op.add_column(sa.Column('review_request_template', sa.String(length=250), nullable=False, server_default=DEFAULT_TEMPLATE))


def downgrade() -> None:
    with op.batch_alter_table('shop_settings', schema=None) as batch_op:
        batch_op.drop_column('review_request_template')
        batch_op.drop_column('review_request_delay_days')
        batch_op.drop_column('review_request_enabled')
    with op.batch_alter_table('consents', schema=None) as batch_op:
        batch_op.drop_column('purpose')
