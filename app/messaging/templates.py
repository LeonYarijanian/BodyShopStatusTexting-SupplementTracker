"""Stage templates and rendering (Section 6)."""

import re

import phonenumbers

from app.enums import Stage

DEFAULT_TEMPLATES: dict[Stage, str | None] = {
    Stage.CHECKED_IN: (
        "Hi {first_name}, this is {shop_name}. Your {vehicle} is checked in (RO {ro_number}). "
        "We'll text you as the repair moves along. Reply STOP to opt out, HELP for help."
    ),
    Stage.WAITING_ON_INSURANCE: (
        "{shop_name}: We sent the estimate for your {vehicle} to your insurance company and are "
        "waiting for their approval. We'll update you when we hear back."
    ),
    Stage.TEARDOWN: "{shop_name}: We're taking apart the damaged area of your {vehicle} to check for hidden damage.",
    Stage.SUPPLEMENT_PENDING: (
        "{shop_name}: We found more damage on your {vehicle} and asked your insurance company to "
        "approve the extra repairs. We'll keep you posted."
    ),
    Stage.PARTS_ORDERED: "{shop_name}: Parts for your {vehicle} are ordered. We'll start repairs as soon as they arrive.",
    Stage.PARTS_RECEIVED: "{shop_name}: Parts for your {vehicle} have arrived.",
    Stage.BODY_REPAIR: "{shop_name}: Repairs on your {vehicle} have started.",
    Stage.PAINT: "{shop_name}: Your {vehicle} is in the paint department.",
    Stage.REASSEMBLY: "{shop_name}: Your {vehicle} is being put back together.",
    Stage.QUALITY_CHECK: "{shop_name}: Your {vehicle} is in final quality check.",
    Stage.READY_FOR_PICKUP: "{shop_name}: Your {vehicle} is ready for pickup! Call {shop_phone} with any questions.",
    Stage.DELIVERED: "{shop_name}: Thanks for trusting us with your {vehicle}.{review_link_sentence}",
    Stage.ON_HOLD: "{shop_name}: The repair on your {vehicle} is on hold. We'll call you with details.",
    Stage.CANCELLED: None,
}

DEFAULT_TEXT_ENABLED: dict[Stage, bool] = {
    Stage.CHECKED_IN: True,
    Stage.WAITING_ON_INSURANCE: True,
    Stage.TEARDOWN: False,
    Stage.SUPPLEMENT_PENDING: True,
    Stage.PARTS_ORDERED: True,
    Stage.PARTS_RECEIVED: False,
    Stage.BODY_REPAIR: True,
    Stage.PAINT: True,
    Stage.REASSEMBLY: False,
    Stage.QUALITY_CHECK: False,
    Stage.READY_FOR_PICKUP: True,
    Stage.DELIVERED: True,
    Stage.ON_HOLD: False,
    Stage.CANCELLED: False,
}

# Stages whose text can be switched on or off and edited. CANCELLED never texts.
TEXTABLE_STAGES = tuple(s for s in Stage if s != Stage.CANCELLED)

KNOWN_VARIABLES = ("first_name", "shop_name", "shop_phone", "vehicle", "ro_number", "review_link_sentence")
MAX_TEMPLATE_LENGTH = 250
MAX_TEXT_LENGTH = 320
STOP_SUFFIX = "Reply STOP to opt out."
REVIEW_SENTENCE = "If you have a minute, a review helps us a lot: "

PREVIEW_FIRST_NAME = "Maria"
PREVIEW_VEHICLE = "2021 Honda Accord"
PREVIEW_RO_NUMBER = "24-1187"

_VARIABLE_RE = re.compile(r"\{([^{}]*)\}")


def default_stage_text_enabled() -> dict[str, bool]:
    return {stage.value: enabled for stage, enabled in DEFAULT_TEXT_ENABLED.items()}


def default_stage_templates() -> dict[str, str]:
    return {stage.value: text for stage, text in DEFAULT_TEMPLATES.items() if text is not None}


class TemplateError(ValueError):
    pass


def validate_template(text: str) -> None:
    """Rendering rules 1 and 5, checked when a template is saved."""
    for match in _VARIABLE_RE.finditer(text):
        if match.group(1) not in KNOWN_VARIABLES:
            raise TemplateError(f"Unknown variable {{{match.group(1)}}}")
    if len(text) > MAX_TEMPLATE_LENGTH:
        raise TemplateError(f"Template is longer than {MAX_TEMPLATE_LENGTH} characters ({len(text)}).")


def parse_us_phone(text: str) -> str:
    """Parse any US phone format to E.164. Raises ValueError for an invalid number."""
    try:
        parsed = phonenumbers.parse((text or "").strip(), "US")
    except phonenumbers.NumberParseException as exc:
        raise ValueError(f"Invalid phone number: {text}") from exc
    if not phonenumbers.is_valid_number(parsed):
        raise ValueError(f"Invalid phone number: {text}")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def format_us_phone(phone_e164: str) -> str:
    """+18185550100 -> (818) 555-0100."""
    try:
        parsed = phonenumbers.parse(phone_e164, "US")
    except phonenumbers.NumberParseException:
        return phone_e164
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.NATIONAL)


def review_link_sentence(review_url: str) -> str:
    return f" {REVIEW_SENTENCE}{review_url}" if review_url else ""


def template_variables(*, first_name: str, shop_name: str, shop_phone_e164: str, vehicle: str, ro_number: str, review_url: str) -> dict[str, str]:
    return {
        "first_name": first_name,
        "shop_name": shop_name,
        "shop_phone": format_us_phone(shop_phone_e164),
        "vehicle": vehicle,
        "ro_number": ro_number,
        "review_link_sentence": review_link_sentence(review_url),
    }


def replace_variables(template: str, variables: dict[str, str]) -> str:
    """Rendering rule 1: replace every variable exactly."""

    def _sub(match: re.Match) -> str:
        name = match.group(1)
        if name not in variables:
            raise TemplateError(f"Unknown variable {{{name}}}")
        return variables[name]

    return _VARIABLE_RE.sub(_sub, template)


def add_identification(text: str, shop_name: str) -> str:
    """Rendering rule 2: the shop is named in every text."""
    if shop_name not in text:
        return f"{shop_name}: {text}"
    return text


def add_stop_suffix(text: str) -> str:
    """Rendering rule 3 (the caller decides whether this is the first message to the number)."""
    if "STOP" in text:
        return text
    return f"{text} {STOP_SUFFIX}" if text and not text.endswith(" ") else f"{text}{STOP_SUFFIX}"
