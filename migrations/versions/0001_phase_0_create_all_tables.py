"""Phase 0: create all 13 tables (Section 5)

Revision ID: 0001
Revises: 
Create Date: 2026-10-02 04:59:08.404158
"""
from alembic import op
import sqlalchemy as sa


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('login_attempts',
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('attempted_at', sa.DateTime(), nullable=False),
    sa.Column('succeeded', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('login_attempts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_login_attempts_email'), ['email'], unique=False)

    op.create_table('shops',
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('phone_e164', sa.String(length=16), nullable=False),
    sa.Column('timezone', sa.String(length=64), nullable=False),
    sa.Column('address', sa.String(length=200), nullable=True),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('customers',
    sa.Column('shop_id', sa.Integer(), nullable=False),
    sa.Column('first_name', sa.String(length=60), nullable=False),
    sa.Column('last_name', sa.String(length=60), nullable=True),
    sa.Column('phone_e164', sa.String(length=16), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=True),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['shop_id'], ['shops.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('shop_id', 'phone_e164', name='uq_customers_shop_phone')
    )
    with op.batch_alter_table('customers', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_customers_shop_id'), ['shop_id'], unique=False)

    op.create_table('insurers',
    sa.Column('shop_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('is_drp', sa.Boolean(), nullable=False),
    sa.Column('follow_up_interval_business_days', sa.Integer(), nullable=True),
    sa.Column('claims_email', sa.String(length=254), nullable=True),
    sa.Column('claims_phone_e164', sa.String(length=16), nullable=True),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['shop_id'], ['shops.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('insurers', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_insurers_shop_id'), ['shop_id'], unique=False)
        batch_op.create_index('uq_insurers_shop_name_ci', ['shop_id', sa.text('lower(name)')], unique=True)

    op.create_table('shop_settings',
    sa.Column('shop_id', sa.Integer(), nullable=False),
    sa.Column('messaging_mode', sa.Enum('DEMO', 'LIVE', name='messagingmode', native_enum=False, length=32), nullable=False),
    sa.Column('quiet_start', sa.Time(), nullable=False),
    sa.Column('quiet_end', sa.Time(), nullable=False),
    sa.Column('cool_off_minutes', sa.Integer(), nullable=False),
    sa.Column('daily_cap', sa.Integer(), nullable=False),
    sa.Column('review_url', sa.String(length=500), nullable=False),
    sa.Column('stage_text_enabled', sa.JSON(), nullable=False),
    sa.Column('stage_templates', sa.JSON(), nullable=False),
    sa.Column('default_follow_up_interval_business_days', sa.Integer(), nullable=False),
    sa.Column('follow_up_due_time', sa.Time(), nullable=False),
    sa.Column('concentration_warning_pct', sa.Integer(), nullable=False),
    sa.Column('twilio_from_e164', sa.String(length=16), nullable=False),
    sa.Column('twilio_messaging_service_sid', sa.String(length=64), nullable=False),
    sa.Column('a2p_10dlc_approved', sa.Boolean(), nullable=False),
    sa.Column('consent_script_confirmed', sa.Boolean(), nullable=False),
    sa.Column('twilio_handles_keyword_replies', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['shop_id'], ['shops.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('shop_settings', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_shop_settings_shop_id'), ['shop_id'], unique=True)

    op.create_table('users',
    sa.Column('shop_id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('password_hash', sa.String(), nullable=False),
    sa.Column('full_name', sa.String(length=120), nullable=False),
    sa.Column('role', sa.Enum('ADMIN', 'STAFF', name='role', native_enum=False, length=32), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['shop_id'], ['shops.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_shop_id'), ['shop_id'], unique=False)

    op.create_table('adjusters',
    sa.Column('shop_id', sa.Integer(), nullable=False),
    sa.Column('insurer_id', sa.Integer(), nullable=False),
    sa.Column('full_name', sa.String(length=120), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=True),
    sa.Column('phone_e164', sa.String(length=16), nullable=True),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['insurer_id'], ['insurers.id'], ),
    sa.ForeignKeyConstraint(['shop_id'], ['shops.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('adjusters', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_adjusters_insurer_id'), ['insurer_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_adjusters_shop_id'), ['shop_id'], unique=False)

    op.create_table('consents',
    sa.Column('shop_id', sa.Integer(), nullable=False),
    sa.Column('customer_id', sa.Integer(), nullable=False),
    sa.Column('phone_e164', sa.String(length=16), nullable=False),
    sa.Column('status', sa.Enum('OPTED_IN', 'OPTED_OUT', name='consentstatus', native_enum=False, length=32), nullable=False),
    sa.Column('method', sa.Enum('IN_PERSON_VERBAL', 'SIGNED_FORM', 'KEYWORD', 'IMPORTED', name='consentmethod', native_enum=False, length=32), nullable=False),
    sa.Column('recorded_by_user_id', sa.Integer(), nullable=True),
    sa.Column('recorded_at', sa.DateTime(), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
    sa.ForeignKeyConstraint(['recorded_by_user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['shop_id'], ['shops.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('consents', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_consents_customer_id'), ['customer_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_consents_shop_id'), ['shop_id'], unique=False)
        batch_op.create_index('ix_consents_shop_id_phone_e164_recorded_at', ['shop_id', 'phone_e164', 'recorded_at'], unique=False)

    op.create_table('repair_orders',
    sa.Column('shop_id', sa.Integer(), nullable=False),
    sa.Column('ro_number', sa.String(length=20), nullable=False),
    sa.Column('customer_id', sa.Integer(), nullable=False),
    sa.Column('vehicle_year', sa.Integer(), nullable=False),
    sa.Column('vehicle_make', sa.String(length=40), nullable=False),
    sa.Column('vehicle_model', sa.String(length=60), nullable=False),
    sa.Column('vehicle_color', sa.String(length=30), nullable=True),
    sa.Column('vin', sa.String(length=17), nullable=True),
    sa.Column('payer_type', sa.Enum('INSURANCE', 'CUSTOMER_PAY', name='payertype', native_enum=False, length=32), nullable=False),
    sa.Column('insurer_id', sa.Integer(), nullable=True),
    sa.Column('adjuster_id', sa.Integer(), nullable=True),
    sa.Column('claim_number', sa.String(length=40), nullable=True),
    sa.Column('original_estimate_cents', sa.Integer(), nullable=False),
    sa.Column('final_invoice_cents', sa.Integer(), nullable=True),
    sa.Column('current_stage', sa.Enum('CHECKED_IN', 'WAITING_ON_INSURANCE', 'TEARDOWN', 'SUPPLEMENT_PENDING', 'PARTS_ORDERED', 'PARTS_RECEIVED', 'BODY_REPAIR', 'PAINT', 'REASSEMBLY', 'QUALITY_CHECK', 'READY_FOR_PICKUP', 'DELIVERED', 'ON_HOLD', 'CANCELLED', name='stage', native_enum=False, length=32), nullable=False),
    sa.Column('checked_in_at', sa.DateTime(), nullable=False),
    sa.Column('delivered_at', sa.DateTime(), nullable=True),
    sa.Column('needs_reply', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['adjuster_id'], ['adjusters.id'], ),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
    sa.ForeignKeyConstraint(['insurer_id'], ['insurers.id'], ),
    sa.ForeignKeyConstraint(['shop_id'], ['shops.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('shop_id', 'ro_number', name='uq_repair_orders_shop_ro_number')
    )
    with op.batch_alter_table('repair_orders', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_repair_orders_adjuster_id'), ['adjuster_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_repair_orders_customer_id'), ['customer_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_repair_orders_insurer_id'), ['insurer_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_repair_orders_shop_id'), ['shop_id'], unique=False)
        batch_op.create_index('ix_repair_orders_shop_id_current_stage', ['shop_id', 'current_stage'], unique=False)

    op.create_table('messages',
    sa.Column('shop_id', sa.Integer(), nullable=False),
    sa.Column('repair_order_id', sa.Integer(), nullable=True),
    sa.Column('customer_id', sa.Integer(), nullable=True),
    sa.Column('direction', sa.Enum('OUTBOUND', 'INBOUND', name='messagedirection', native_enum=False, length=32), nullable=False),
    sa.Column('kind', sa.Enum('STAGE_UPDATE', 'MANUAL', 'OPT_OUT_CONFIRMATION', 'OPT_IN_CONFIRMATION', 'HELP_REPLY', 'INBOUND_REPLY', 'INBOUND_KEYWORD', name='messagekind', native_enum=False, length=32), nullable=False),
    sa.Column('status', sa.Enum('SCHEDULED', 'SENT', 'DELIVERED', 'FAILED', 'CANCELLED_SUPERSEDED', 'BLOCKED_NO_CONSENT', 'BLOCKED_OPTED_OUT', 'RECEIVED', name='messagestatus', native_enum=False, length=32), nullable=False),
    sa.Column('to_e164', sa.String(length=16), nullable=False),
    sa.Column('from_e164', sa.String(length=16), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('stage', sa.Enum('CHECKED_IN', 'WAITING_ON_INSURANCE', 'TEARDOWN', 'SUPPLEMENT_PENDING', 'PARTS_ORDERED', 'PARTS_RECEIVED', 'BODY_REPAIR', 'PAINT', 'REASSEMBLY', 'QUALITY_CHECK', 'READY_FOR_PICKUP', 'DELIVERED', 'ON_HOLD', 'CANCELLED', name='stage', native_enum=False, length=32), nullable=True),
    sa.Column('scheduled_send_at', sa.DateTime(), nullable=True),
    sa.Column('sent_at', sa.DateTime(), nullable=True),
    sa.Column('provider_message_id', sa.String(length=64), nullable=True),
    sa.Column('error_text', sa.String(length=200), nullable=True),
    sa.Column('created_by_user_id', sa.Integer(), nullable=True),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
    sa.ForeignKeyConstraint(['repair_order_id'], ['repair_orders.id'], ),
    sa.ForeignKeyConstraint(['shop_id'], ['shops.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_messages_customer_id'), ['customer_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_messages_provider_message_id'), ['provider_message_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_messages_repair_order_id'), ['repair_order_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_messages_shop_id'), ['shop_id'], unique=False)
        batch_op.create_index('ix_messages_status_scheduled_send_at', ['status', 'scheduled_send_at'], unique=False)

    op.create_table('stage_events',
    sa.Column('shop_id', sa.Integer(), nullable=False),
    sa.Column('repair_order_id', sa.Integer(), nullable=False),
    sa.Column('from_stage', sa.Enum('CHECKED_IN', 'WAITING_ON_INSURANCE', 'TEARDOWN', 'SUPPLEMENT_PENDING', 'PARTS_ORDERED', 'PARTS_RECEIVED', 'BODY_REPAIR', 'PAINT', 'REASSEMBLY', 'QUALITY_CHECK', 'READY_FOR_PICKUP', 'DELIVERED', 'ON_HOLD', 'CANCELLED', name='stage', native_enum=False, length=32), nullable=True),
    sa.Column('to_stage', sa.Enum('CHECKED_IN', 'WAITING_ON_INSURANCE', 'TEARDOWN', 'SUPPLEMENT_PENDING', 'PARTS_ORDERED', 'PARTS_RECEIVED', 'BODY_REPAIR', 'PAINT', 'REASSEMBLY', 'QUALITY_CHECK', 'READY_FOR_PICKUP', 'DELIVERED', 'ON_HOLD', 'CANCELLED', name='stage', native_enum=False, length=32), nullable=False),
    sa.Column('changed_by_user_id', sa.Integer(), nullable=False),
    sa.Column('changed_at', sa.DateTime(), nullable=False),
    sa.Column('note', sa.String(length=200), nullable=True),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['changed_by_user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['repair_order_id'], ['repair_orders.id'], ),
    sa.ForeignKeyConstraint(['shop_id'], ['shops.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('stage_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_stage_events_repair_order_id'), ['repair_order_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_stage_events_shop_id'), ['shop_id'], unique=False)

    op.create_table('supplements',
    sa.Column('shop_id', sa.Integer(), nullable=False),
    sa.Column('repair_order_id', sa.Integer(), nullable=False),
    sa.Column('sequence_number', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('DRAFT', 'SUBMITTED', 'APPROVED', 'PARTIALLY_APPROVED', 'DENIED', 'WITHDRAWN', name='supplementstatus', native_enum=False, length=32), nullable=False),
    sa.Column('description', sa.String(length=500), nullable=False),
    sa.Column('requested_cents', sa.Integer(), nullable=False),
    sa.Column('approved_cents', sa.Integer(), nullable=False),
    sa.Column('adjuster_id', sa.Integer(), nullable=True),
    sa.Column('submitted_at', sa.DateTime(), nullable=True),
    sa.Column('decided_at', sa.DateTime(), nullable=True),
    sa.Column('next_follow_up_due_at', sa.DateTime(), nullable=True),
    sa.Column('follow_up_count', sa.Integer(), nullable=False),
    sa.Column('last_follow_up_at', sa.DateTime(), nullable=True),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['adjuster_id'], ['adjusters.id'], ),
    sa.ForeignKeyConstraint(['repair_order_id'], ['repair_orders.id'], ),
    sa.ForeignKeyConstraint(['shop_id'], ['shops.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('repair_order_id', 'sequence_number', name='uq_supplements_ro_sequence')
    )
    with op.batch_alter_table('supplements', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_supplements_adjuster_id'), ['adjuster_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_supplements_repair_order_id'), ['repair_order_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_supplements_shop_id'), ['shop_id'], unique=False)
        batch_op.create_index('ix_supplements_shop_id_status', ['shop_id', 'status'], unique=False)

    op.create_table('supplement_events',
    sa.Column('shop_id', sa.Integer(), nullable=False),
    sa.Column('supplement_id', sa.Integer(), nullable=False),
    sa.Column('event_type', sa.Enum('STATUS_CHANGE', 'FOLLOW_UP', 'NOTE', name='supplementeventtype', native_enum=False, length=32), nullable=False),
    sa.Column('from_status', sa.Enum('DRAFT', 'SUBMITTED', 'APPROVED', 'PARTIALLY_APPROVED', 'DENIED', 'WITHDRAWN', name='supplementstatus', native_enum=False, length=32), nullable=True),
    sa.Column('to_status', sa.Enum('DRAFT', 'SUBMITTED', 'APPROVED', 'PARTIALLY_APPROVED', 'DENIED', 'WITHDRAWN', name='supplementstatus', native_enum=False, length=32), nullable=True),
    sa.Column('follow_up_method', sa.Enum('PHONE', 'EMAIL', 'INSURER_PORTAL', 'OTHER', name='followupmethod', native_enum=False, length=32), nullable=True),
    sa.Column('note', sa.String(length=500), nullable=True),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('occurred_at', sa.DateTime(), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['shop_id'], ['shops.id'], ),
    sa.ForeignKeyConstraint(['supplement_id'], ['supplements.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('supplement_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_supplement_events_shop_id'), ['shop_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_supplement_events_supplement_id'), ['supplement_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('supplement_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_supplement_events_supplement_id'))
        batch_op.drop_index(batch_op.f('ix_supplement_events_shop_id'))

    op.drop_table('supplement_events')
    with op.batch_alter_table('supplements', schema=None) as batch_op:
        batch_op.drop_index('ix_supplements_shop_id_status')
        batch_op.drop_index(batch_op.f('ix_supplements_shop_id'))
        batch_op.drop_index(batch_op.f('ix_supplements_repair_order_id'))
        batch_op.drop_index(batch_op.f('ix_supplements_adjuster_id'))

    op.drop_table('supplements')
    with op.batch_alter_table('stage_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_stage_events_shop_id'))
        batch_op.drop_index(batch_op.f('ix_stage_events_repair_order_id'))

    op.drop_table('stage_events')
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.drop_index('ix_messages_status_scheduled_send_at')
        batch_op.drop_index(batch_op.f('ix_messages_shop_id'))
        batch_op.drop_index(batch_op.f('ix_messages_repair_order_id'))
        batch_op.drop_index(batch_op.f('ix_messages_provider_message_id'))
        batch_op.drop_index(batch_op.f('ix_messages_customer_id'))

    op.drop_table('messages')
    with op.batch_alter_table('repair_orders', schema=None) as batch_op:
        batch_op.drop_index('ix_repair_orders_shop_id_current_stage')
        batch_op.drop_index(batch_op.f('ix_repair_orders_shop_id'))
        batch_op.drop_index(batch_op.f('ix_repair_orders_insurer_id'))
        batch_op.drop_index(batch_op.f('ix_repair_orders_customer_id'))
        batch_op.drop_index(batch_op.f('ix_repair_orders_adjuster_id'))

    op.drop_table('repair_orders')
    with op.batch_alter_table('consents', schema=None) as batch_op:
        batch_op.drop_index('ix_consents_shop_id_phone_e164_recorded_at')
        batch_op.drop_index(batch_op.f('ix_consents_shop_id'))
        batch_op.drop_index(batch_op.f('ix_consents_customer_id'))

    op.drop_table('consents')
    with op.batch_alter_table('adjusters', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_adjusters_shop_id'))
        batch_op.drop_index(batch_op.f('ix_adjusters_insurer_id'))

    op.drop_table('adjusters')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_shop_id'))

    op.drop_table('users')
    with op.batch_alter_table('shop_settings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_shop_settings_shop_id'))

    op.drop_table('shop_settings')
    with op.batch_alter_table('insurers', schema=None) as batch_op:
        batch_op.drop_index('uq_insurers_shop_name_ci')
        batch_op.drop_index(batch_op.f('ix_insurers_shop_id'))

    op.drop_table('insurers')
    with op.batch_alter_table('customers', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_customers_shop_id'))

    op.drop_table('customers')
    op.drop_table('shops')
    with op.batch_alter_table('login_attempts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_login_attempts_email'))

    op.drop_table('login_attempts')
