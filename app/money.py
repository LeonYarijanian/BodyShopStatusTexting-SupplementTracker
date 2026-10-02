"""Dollars-string <-> integer cents, and display formatting. Money is never a float."""

import re
from decimal import ROUND_HALF_UP, Decimal

_DOLLARS_RE = re.compile(r"^\d+(\.\d{1,2})?$")


def dollars_to_cents(text: str) -> int:
    """Parse a dollar amount such as "4,250.50" or "$1,800" into cents.

    `$` and `,` are stripped. At most 2 decimals; 0 or more.
    """
    if text is None:
        raise ValueError("Amount is required.")
    cleaned = str(text).strip().replace("$", "").replace(",", "")
    if not _DOLLARS_RE.match(cleaned):
        raise ValueError(f"Invalid dollar amount: {text}")
    return int(Decimal(cleaned) * 100)


def format_cents(cents: int) -> str:
    """317050 -> "$3,170.50"."""
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    return f"{sign}${cents // 100:,}.{cents % 100:02d}"


def cents_to_input(cents: int | None) -> str:
    """Value for a dollars form field: 425050 -> "4250.50"."""
    if cents is None:
        return ""
    return f"{cents // 100}.{cents % 100:02d}"


def round_half_up(value: Decimal | int, places: int = 1) -> Decimal:
    quantum = Decimal(1).scaleb(-places)
    return Decimal(value).quantize(quantum, rounding=ROUND_HALF_UP)


def format_decimal(value: Decimal | int, places: int = 1) -> str:
    """Display a decimal with ROUND_HALF_UP, e.g. 86.666 -> "86.7"."""
    return str(round_half_up(value, places))
