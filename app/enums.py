"""Every enum in the app (Sections 5 to 8). Values are stored as strings."""

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "ADMIN"
    STAFF = "STAFF"


class PayerType(StrEnum):
    INSURANCE = "INSURANCE"
    CUSTOMER_PAY = "CUSTOMER_PAY"


class Stage(StrEnum):
    CHECKED_IN = "CHECKED_IN"
    WAITING_ON_INSURANCE = "WAITING_ON_INSURANCE"
    TEARDOWN = "TEARDOWN"
    SUPPLEMENT_PENDING = "SUPPLEMENT_PENDING"
    PARTS_ORDERED = "PARTS_ORDERED"
    PARTS_RECEIVED = "PARTS_RECEIVED"
    BODY_REPAIR = "BODY_REPAIR"
    PAINT = "PAINT"
    REASSEMBLY = "REASSEMBLY"
    QUALITY_CHECK = "QUALITY_CHECK"
    READY_FOR_PICKUP = "READY_FOR_PICKUP"
    DELIVERED = "DELIVERED"
    ON_HOLD = "ON_HOLD"
    CANCELLED = "CANCELLED"


class ConsentStatus(StrEnum):
    OPTED_IN = "OPTED_IN"
    OPTED_OUT = "OPTED_OUT"


class ConsentPurpose(StrEnum):
    """Section 16 item 6: review requests need their own consent, separate from repair updates."""

    REPAIR_UPDATES = "REPAIR_UPDATES"
    REVIEW_REQUESTS = "REVIEW_REQUESTS"


class ConsentMethod(StrEnum):
    IN_PERSON_VERBAL = "IN_PERSON_VERBAL"
    SIGNED_FORM = "SIGNED_FORM"
    KEYWORD = "KEYWORD"
    IMPORTED = "IMPORTED"


class MessageDirection(StrEnum):
    OUTBOUND = "OUTBOUND"
    INBOUND = "INBOUND"


class MessageKind(StrEnum):
    STAGE_UPDATE = "STAGE_UPDATE"
    MANUAL = "MANUAL"
    OPT_OUT_CONFIRMATION = "OPT_OUT_CONFIRMATION"
    OPT_IN_CONFIRMATION = "OPT_IN_CONFIRMATION"
    HELP_REPLY = "HELP_REPLY"
    INBOUND_REPLY = "INBOUND_REPLY"
    INBOUND_KEYWORD = "INBOUND_KEYWORD"
    REVIEW_REQUEST = "REVIEW_REQUEST"


class MessageStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    CANCELLED_SUPERSEDED = "CANCELLED_SUPERSEDED"
    BLOCKED_NO_CONSENT = "BLOCKED_NO_CONSENT"
    BLOCKED_OPTED_OUT = "BLOCKED_OPTED_OUT"
    RECEIVED = "RECEIVED"


class MessagingMode(StrEnum):
    DEMO = "DEMO"
    LIVE = "LIVE"


class SupplementStatus(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    PARTIALLY_APPROVED = "PARTIALLY_APPROVED"
    DENIED = "DENIED"
    WITHDRAWN = "WITHDRAWN"


class SupplementEventType(StrEnum):
    STATUS_CHANGE = "STATUS_CHANGE"
    FOLLOW_UP = "FOLLOW_UP"
    NOTE = "NOTE"


class FollowUpMethod(StrEnum):
    PHONE = "PHONE"
    EMAIL = "EMAIL"
    INSURER_PORTAL = "INSURER_PORTAL"
    OTHER = "OTHER"


class AgingBucket(StrEnum):
    FRESH = "FRESH"
    WATCH = "WATCH"
    LATE = "LATE"
    CRITICAL = "CRITICAL"


# Section 6: staff labels for each stage.
STAGE_LABELS: dict[Stage, str] = {
    Stage.CHECKED_IN: "Checked in",
    Stage.WAITING_ON_INSURANCE: "Waiting on insurance",
    Stage.TEARDOWN: "Teardown",
    Stage.SUPPLEMENT_PENDING: "Supplement pending",
    Stage.PARTS_ORDERED: "Parts ordered",
    Stage.PARTS_RECEIVED: "Parts received",
    Stage.BODY_REPAIR: "Body repair",
    Stage.PAINT: "Paint",
    Stage.REASSEMBLY: "Reassembly",
    Stage.QUALITY_CHECK: "Quality check",
    Stage.READY_FOR_PICKUP: "Ready for pickup",
    Stage.DELIVERED: "Delivered",
    Stage.ON_HOLD: "On hold",
    Stage.CANCELLED: "Cancelled",
}

TERMINAL_STAGES = (Stage.DELIVERED, Stage.CANCELLED)
ACTIVE_STAGES = tuple(s for s in Stage if s not in TERMINAL_STAGES)

FOLLOW_UP_METHOD_LABELS: dict[FollowUpMethod, str] = {
    FollowUpMethod.PHONE: "Phone",
    FollowUpMethod.EMAIL: "Email",
    FollowUpMethod.INSURER_PORTAL: "Insurer portal",
    FollowUpMethod.OTHER: "Other",
}
