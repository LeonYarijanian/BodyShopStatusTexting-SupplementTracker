"""SQLAlchemy models: exactly the 13 tables in Section 5."""

import datetime as dt

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.business_days import UTC, utcnow
from app.messaging.templates import default_review_request_template, default_stage_templates, default_stage_text_enabled
from app.enums import (
    AdjusterEmailStatus,
    DraftSource,
    ConsentMethod,
    ConsentPurpose,
    ConsentStatus,
    FollowUpMethod,
    MessageDirection,
    MessageKind,
    MessageStatus,
    MessagingMode,
    PayerType,
    Role,
    Stage,
    SupplementEventType,
    SupplementStatus,
)


class UTCDateTime(TypeDecorator):
    """Stores naive UTC in the database and always returns timezone-aware UTC datetimes."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Naive datetimes are not allowed; use UTC-aware datetimes.")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


def enum_column(enum_cls):
    return SAEnum(enum_cls, native_enum=False, length=32, validate_strings=True)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)


def shop_fk() -> Mapped[int]:
    return mapped_column(ForeignKey("shops.id"), nullable=False, index=True)


class Shop(TimestampMixin, Base):
    __tablename__ = "shops"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone_e164: Mapped[str] = mapped_column(String(16), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="America/Los_Angeles")
    address: Mapped[str | None] = mapped_column(String(200), nullable=True)

    settings: Mapped["ShopSettings"] = relationship(back_populates="shop", uselist=False)


class ShopSettings(TimestampMixin, Base):
    __tablename__ = "shop_settings"

    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), nullable=False, unique=True, index=True)
    messaging_mode: Mapped[MessagingMode] = mapped_column(enum_column(MessagingMode), nullable=False, default=MessagingMode.DEMO)
    quiet_start: Mapped[dt.time] = mapped_column(Time, nullable=False, default=dt.time(20, 0))
    quiet_end: Mapped[dt.time] = mapped_column(Time, nullable=False, default=dt.time(8, 0))
    cool_off_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    daily_cap: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    review_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    stage_text_enabled: Mapped[dict] = mapped_column(JSON, nullable=False, default=default_stage_text_enabled)
    stage_templates: Mapped[dict] = mapped_column(JSON, nullable=False, default=default_stage_templates)
    default_follow_up_interval_business_days: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    follow_up_due_time: Mapped[dt.time] = mapped_column(Time, nullable=False, default=dt.time(9, 0))
    concentration_warning_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=40)
    twilio_from_e164: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    twilio_messaging_service_sid: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    a2p_10dlc_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    consent_script_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    twilio_handles_keyword_replies: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Section 16 item 3: daily email digest of follow-ups due.
    digest_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    digest_recipients: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    digest_send_time: Mapped[dt.time] = mapped_column(Time, nullable=False, default=dt.time(7, 30))
    digest_last_sent_on: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    # Section 16 item 6: 1 review request text after delivery, with its own consent.
    review_request_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    review_request_delay_days: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    review_request_template: Mapped[str] = mapped_column(String(250), nullable=False, default=default_review_request_template)

    shop: Mapped[Shop] = relationship(back_populates="settings")


class User(TimestampMixin, Base):
    __tablename__ = "users"

    shop_id: Mapped[int] = shop_fk()
    email: Mapped[str] = mapped_column(String(254), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[Role] = mapped_column(enum_column(Role), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class LoginAttempt(TimestampMixin, Base):
    __tablename__ = "login_attempts"

    email: Mapped[str] = mapped_column(String(254), nullable=False, index=True)
    attempted_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False)


class Customer(TimestampMixin, Base):
    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("shop_id", "phone_e164", name="uq_customers_shop_phone"),)

    shop_id: Mapped[int] = shop_fk()
    first_name: Mapped[str] = mapped_column(String(60), nullable=False)
    last_name: Mapped[str | None] = mapped_column(String(60), nullable=True)
    phone_e164: Mapped[str] = mapped_column(String(16), nullable=False)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    anonymized_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)


class Insurer(TimestampMixin, Base):
    __tablename__ = "insurers"

    shop_id: Mapped[int] = shop_fk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_drp: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    follow_up_interval_business_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    claims_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    claims_phone_e164: Mapped[str | None] = mapped_column(String(16), nullable=True)

    adjusters: Mapped[list["Adjuster"]] = relationship(back_populates="insurer", order_by="Adjuster.full_name")


# Insurer names are unique per shop, ignoring case.
Index("uq_insurers_shop_name_ci", Insurer.shop_id, func.lower(Insurer.name), unique=True)


class Adjuster(TimestampMixin, Base):
    __tablename__ = "adjusters"

    shop_id: Mapped[int] = shop_fk()
    insurer_id: Mapped[int] = mapped_column(ForeignKey("insurers.id"), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    phone_e164: Mapped[str | None] = mapped_column(String(16), nullable=True)

    insurer: Mapped[Insurer] = relationship(back_populates="adjusters")


class RepairOrder(TimestampMixin, Base):
    __tablename__ = "repair_orders"
    __table_args__ = (
        UniqueConstraint("shop_id", "ro_number", name="uq_repair_orders_shop_ro_number"),
        Index("ix_repair_orders_shop_id_current_stage", "shop_id", "current_stage"),
    )

    shop_id: Mapped[int] = shop_fk()
    ro_number: Mapped[str] = mapped_column(String(20), nullable=False)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    vehicle_year: Mapped[int] = mapped_column(Integer, nullable=False)
    vehicle_make: Mapped[str] = mapped_column(String(40), nullable=False)
    vehicle_model: Mapped[str] = mapped_column(String(60), nullable=False)
    vehicle_color: Mapped[str | None] = mapped_column(String(30), nullable=True)
    vin: Mapped[str | None] = mapped_column(String(17), nullable=True)
    payer_type: Mapped[PayerType] = mapped_column(enum_column(PayerType), nullable=False)
    insurer_id: Mapped[int | None] = mapped_column(ForeignKey("insurers.id"), nullable=True, index=True)
    adjuster_id: Mapped[int | None] = mapped_column(ForeignKey("adjusters.id"), nullable=True, index=True)
    claim_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    original_estimate_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    final_invoice_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_stage: Mapped[Stage] = mapped_column(enum_column(Stage), nullable=False)
    checked_in_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)
    delivered_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    needs_reply: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    customer: Mapped[Customer] = relationship()
    insurer: Mapped[Insurer | None] = relationship()
    adjuster: Mapped[Adjuster | None] = relationship()

    @property
    def vehicle(self) -> str:
        return f"{self.vehicle_year} {self.vehicle_make} {self.vehicle_model}"


class StageEvent(TimestampMixin, Base):
    __tablename__ = "stage_events"

    shop_id: Mapped[int] = shop_fk()
    repair_order_id: Mapped[int] = mapped_column(ForeignKey("repair_orders.id"), nullable=False, index=True)
    from_stage: Mapped[Stage | None] = mapped_column(enum_column(Stage), nullable=True)
    to_stage: Mapped[Stage] = mapped_column(enum_column(Stage), nullable=False)
    changed_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    changed_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)

    changed_by: Mapped[User] = relationship()


class Consent(TimestampMixin, Base):
    __tablename__ = "consents"
    __table_args__ = (Index("ix_consents_shop_id_phone_e164_recorded_at", "shop_id", "phone_e164", "recorded_at"),)

    shop_id: Mapped[int] = shop_fk()
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    phone_e164: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[ConsentStatus] = mapped_column(enum_column(ConsentStatus), nullable=False)
    method: Mapped[ConsentMethod] = mapped_column(enum_column(ConsentMethod), nullable=False)
    purpose: Mapped[ConsentPurpose] = mapped_column(
        enum_column(ConsentPurpose), nullable=False, default=ConsentPurpose.REPAIR_UPDATES, server_default=ConsentPurpose.REPAIR_UPDATES.value
    )
    recorded_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    recorded_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)


class Message(TimestampMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_status_scheduled_send_at", "status", "scheduled_send_at"),)

    shop_id: Mapped[int] = shop_fk()
    repair_order_id: Mapped[int | None] = mapped_column(ForeignKey("repair_orders.id"), nullable=True, index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    direction: Mapped[MessageDirection] = mapped_column(enum_column(MessageDirection), nullable=False)
    kind: Mapped[MessageKind] = mapped_column(enum_column(MessageKind), nullable=False)
    status: Mapped[MessageStatus] = mapped_column(enum_column(MessageStatus), nullable=False)
    to_e164: Mapped[str] = mapped_column(String(16), nullable=False)
    from_e164: Mapped[str] = mapped_column(String(16), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    stage: Mapped[Stage | None] = mapped_column(enum_column(Stage), nullable=True)
    scheduled_send_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    sent_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    error_text: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # Section 16 item 5: 1 photo sent as a picture message. The file lives in ./media/<token>.<ext>.
    media_token: Mapped[str | None] = mapped_column(String(40), nullable=True, unique=True, index=True)
    media_content_type: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Supplement(TimestampMixin, Base):
    __tablename__ = "supplements"
    __table_args__ = (
        UniqueConstraint("repair_order_id", "sequence_number", name="uq_supplements_ro_sequence"),
        Index("ix_supplements_shop_id_status", "shop_id", "status"),
    )

    shop_id: Mapped[int] = shop_fk()
    repair_order_id: Mapped[int] = mapped_column(ForeignKey("repair_orders.id"), nullable=False, index=True)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[SupplementStatus] = mapped_column(enum_column(SupplementStatus), nullable=False, default=SupplementStatus.DRAFT)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    requested_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    approved_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    adjuster_id: Mapped[int | None] = mapped_column(ForeignKey("adjusters.id"), nullable=True, index=True)
    submitted_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    decided_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    next_follow_up_due_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    follow_up_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_follow_up_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)

    repair_order: Mapped[RepairOrder] = relationship()
    adjuster: Mapped[Adjuster | None] = relationship()


class SupplementEvent(TimestampMixin, Base):
    __tablename__ = "supplement_events"

    shop_id: Mapped[int] = shop_fk()
    supplement_id: Mapped[int] = mapped_column(ForeignKey("supplements.id"), nullable=False, index=True)
    event_type: Mapped[SupplementEventType] = mapped_column(enum_column(SupplementEventType), nullable=False)
    from_status: Mapped[SupplementStatus | None] = mapped_column(enum_column(SupplementStatus), nullable=True)
    to_status: Mapped[SupplementStatus | None] = mapped_column(enum_column(SupplementStatus), nullable=True)
    follow_up_method: Mapped[FollowUpMethod | None] = mapped_column(enum_column(FollowUpMethod), nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    occurred_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)

    user: Mapped[User] = relationship()


class AdjusterEmail(TimestampMixin, Base):
    """Section 16 item 7: a follow-up email to an adjuster, drafted by Claude or the template, sent only after approval."""

    __tablename__ = "adjuster_emails"

    shop_id: Mapped[int] = shop_fk()
    supplement_id: Mapped[int] = mapped_column(ForeignKey("supplements.id"), nullable=False, index=True)
    status: Mapped[AdjusterEmailStatus] = mapped_column(enum_column(AdjusterEmailStatus), nullable=False, default=AdjusterEmailStatus.DRAFT)
    source: Mapped[DraftSource] = mapped_column(enum_column(DraftSource), nullable=False)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    sent_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    sent_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    error_text: Mapped[str | None] = mapped_column(String(200), nullable=True)

    supplement: Mapped[Supplement] = relationship()
