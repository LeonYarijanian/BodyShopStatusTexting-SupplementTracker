"""Section 16 item 9: multiple locations per shop.

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'locations',
        sa.Column('shop_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('phone_e164', sa.String(length=16), nullable=False),
        sa.Column('address', sa.String(length=200), nullable=True),
        sa.Column('twilio_from_e164', sa.String(length=16), nullable=False, server_default=''),
        sa.Column('review_url', sa.String(length=500), nullable=False, server_default=''),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['shop_id'], ['shops.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('shop_id', 'name', name='uq_locations_shop_name'),
    )
    with op.batch_alter_table('locations', schema=None) as batch_op:
        batch_op.create_index('ix_locations_shop_id', ['shop_id'], unique=False)
    with op.batch_alter_table('repair_orders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('location_id', sa.Integer(), nullable=True))
        batch_op.create_index('ix_repair_orders_location_id', ['location_id'], unique=False)
        batch_op.create_foreign_key('fk_repair_orders_location_id', 'locations', ['location_id'], ['id'])
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('location_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_users_location_id', 'locations', ['location_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint('fk_users_location_id', type_='foreignkey')
        batch_op.drop_column('location_id')
    with op.batch_alter_table('repair_orders', schema=None) as batch_op:
        batch_op.drop_constraint('fk_repair_orders_location_id', type_='foreignkey')
        batch_op.drop_index('ix_repair_orders_location_id')
        batch_op.drop_column('location_id')
    op.drop_table('locations')
