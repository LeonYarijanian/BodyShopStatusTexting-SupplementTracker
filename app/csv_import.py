"""CSV import (Section 11): upload, dry run, then commit. Never sends a text; saves every row or none."""

import csv
import datetime as dt
import io
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.business_days import local_to_utc, utcnow
from app.enums import ConsentMethod, ConsentStatus, PayerType, Stage
from app.messaging.templates import parse_us_phone
from app.models import Adjuster, Consent, Customer, Insurer, Location, RepairOrder, Shop, StageEvent, User
from app.money import dollars_to_cents
from app.routes.repair_orders import ROError, validate_vehicle_year, validate_vin
from app.status_page import ensure_status_token

IMPORT_TMP_DIR = Path("import_tmp")
MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 1000
UPLOAD_MAX_AGE_SECONDS = 3600
IMPORTED_NOTE = "imported"

COLUMNS = (
    "ro_number",
    "checked_in_date",
    "checked_in_time",
    "customer_first_name",
    "customer_last_name",
    "customer_phone",
    "customer_email",
    "vehicle_year",
    "vehicle_make",
    "vehicle_model",
    "vehicle_color",
    "vin",
    "payer_type",
    "insurer_name",
    "adjuster_name",
    "claim_number",
    "original_estimate",
    "stage",
    "consent",
    "location",  # optional (Section 16 item 9): a location name; empty puts the RO at the importer's location
)
REQUIRED_COLUMNS = (
    "ro_number",
    "checked_in_date",
    "customer_first_name",
    "customer_phone",
    "vehicle_year",
    "vehicle_make",
    "vehicle_model",
    "payer_type",
    "original_estimate",
)
NOT_ALLOWED_STAGES = (Stage.DELIVERED, Stage.CANCELLED)


class ImportFileError(ValueError):
    """The whole file is rejected."""


@dataclass
class RowResult:
    row_number: int
    values: dict
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    clean: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


# ---------------------------------------------------------------- reading the file


def read_rows(data: bytes) -> list[tuple[int, dict]]:
    """Check size, encoding, header and row count before any row is validated.

    Returns (row number, values) pairs. The header is row 1, so the first data row is row 2.
    """
    if len(data) > MAX_BYTES:
        raise ImportFileError("The file is larger than 2 MB.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ImportFileError("The file must be UTF-8 text.") from None
    records = [(n, record) for n, record in enumerate(csv.reader(io.StringIO(text, newline="")), start=1) if any(cell.strip() for cell in record)]
    if not records:
        raise ImportFileError("The file is empty.")
    header_row, header = records[0]
    if header_row != 1:
        raise ImportFileError("The first line must be the header row.")
    data_records = records[1:]
    if len(data_records) > MAX_ROWS:
        raise ImportFileError(f"The file has {len(data_records):,} data rows. The limit is 1,000.")

    names = [name.strip() for name in header]
    unknown = [name for name in names if name not in COLUMNS]
    if unknown:
        raise ImportFileError(f"Unknown column: {unknown[0]}")
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ImportFileError(f"Column appears more than once: {duplicates[0]}")
    missing = [name for name in REQUIRED_COLUMNS if name not in names]
    if missing:
        raise ImportFileError(f"Missing required column: {missing[0]}")

    rows = []
    for row_number, record in data_records:
        values = {name: "" for name in COLUMNS}
        if len(record) != len(names):
            values["_shape_error"] = f"Row has {len(record)} values but the header has {len(names)} columns."
        for name, value in zip(names, record):
            values[name] = value.strip()
        rows.append((row_number, values))
    return rows


# ---------------------------------------------------------------- dry run


def _find_insurer(db: Session, shop_id: int, name: str) -> Insurer | None:
    return db.scalar(select(Insurer).where(Insurer.shop_id == shop_id, func.lower(Insurer.name) == name.lower()))


def _find_adjuster(db: Session, shop_id: int, insurer_id: int, name: str) -> Adjuster | None:
    return db.scalar(
        select(Adjuster).where(Adjuster.shop_id == shop_id, Adjuster.insurer_id == insurer_id, func.lower(Adjuster.full_name) == name.lower())
    )


def validate_row(db: Session, shop: Shop, row_number: int, values: dict, create_missing_insurers: bool, now: dt.datetime, seen_ro_numbers: set[str]) -> RowResult:
    result = RowResult(row_number=row_number, values=values)
    errors = result.errors
    clean = result.clean
    tz = shop.timezone
    if "_shape_error" in values:
        errors.append(values["_shape_error"])

    def text(name: str, label: str, max_len: int | None = None, required: bool = False) -> str:
        value = values.get(name, "")
        if required and not value:
            errors.append(f"{name}: {label} is required.")
        elif max_len and len(value) > max_len:
            errors.append(f"{name}: must be at most {max_len} characters.")
        return value

    ro_number = text("ro_number", "RO number", 20, required=True)
    if ro_number:
        if ro_number in seen_ro_numbers:
            errors.append(f"ro_number: {ro_number} appears more than once in the file.")
        elif db.scalar(select(RepairOrder.id).where(RepairOrder.shop_id == shop.id, RepairOrder.ro_number == ro_number)) is not None:
            errors.append(f"ro_number: {ro_number} already exists in this shop.")
        seen_ro_numbers.add(ro_number)
    clean["ro_number"] = ro_number

    date_text = text("checked_in_date", "Check-in date", required=True)
    time_text = values.get("checked_in_time") or "08:00"
    if date_text:
        try:
            day = dt.date.fromisoformat(date_text)
            if len(date_text) != 10:
                raise ValueError
            hour, minute = time_text.split(":")
            if len(hour) != 2 or len(minute) != 2:
                raise ValueError
            at = dt.time(int(hour), int(minute))
            checked_in_at = local_to_utc(dt.datetime.combine(day, at), tz)
            if checked_in_at > now:
                errors.append("checked_in_date: check-in cannot be in the future.")
            clean["checked_in_at"] = checked_in_at
        except ValueError:
            errors.append("checked_in_date / checked_in_time: use YYYY-MM-DD and HH:MM (24-hour).")

    clean["first_name"] = text("customer_first_name", "Customer first name", 60, required=True)
    clean["last_name"] = text("customer_last_name", "Customer last name", 60) or None
    phone_text = text("customer_phone", "Customer phone", required=True)
    if phone_text:
        try:
            clean["phone_e164"] = parse_us_phone(phone_text)
        except ValueError:
            errors.append(f"customer_phone: {phone_text} is not a valid US phone number.")
    email = text("customer_email", "Customer email", 254)
    if email and "@" not in email:
        errors.append("customer_email: not a valid email address.")
    clean["email"] = email or None

    year_text = text("vehicle_year", "Vehicle year", required=True)
    if year_text:
        try:
            clean["vehicle_year"] = validate_vehicle_year(year_text, now, tz)
        except ROError as exc:
            errors.append(f"vehicle_year: {exc}")
    clean["vehicle_make"] = text("vehicle_make", "Vehicle make", 40, required=True)
    clean["vehicle_model"] = text("vehicle_model", "Vehicle model", 60, required=True)
    clean["vehicle_color"] = text("vehicle_color", "Vehicle color", 30) or None
    try:
        clean["vin"] = validate_vin(values.get("vin", ""))
    except ROError as exc:
        errors.append(f"vin: {exc}")

    payer_text = values.get("payer_type", "").upper()
    if payer_text not in PayerType.__members__:
        errors.append("payer_type: must be INSURANCE or CUSTOMER_PAY.")
        payer = None
    else:
        payer = PayerType(payer_text)
    clean["payer_type"] = payer

    insurer_name = text("insurer_name", "Insurer name", 120)
    adjuster_name = text("adjuster_name", "Adjuster name", 120)
    claim_number = text("claim_number", "Claim number", 40)
    clean["insurer_name"] = insurer_name
    clean["adjuster_name"] = adjuster_name
    clean["claim_number"] = claim_number or None
    if payer == PayerType.INSURANCE:
        if not insurer_name:
            errors.append("insurer_name: required when payer_type is INSURANCE.")
        else:
            insurer = _find_insurer(db, shop.id, insurer_name)
            if insurer is None:
                if create_missing_insurers:
                    result.notes.append(f"Insurer {insurer_name} will be created.")
                else:
                    errors.append(f"insurer_name: {insurer_name} does not exist. Tick Create missing insurers to add it.")
            elif adjuster_name and _find_adjuster(db, shop.id, insurer.id, adjuster_name) is None:
                result.notes.append(f"Adjuster {adjuster_name} will be created.")
            if insurer is None and adjuster_name and create_missing_insurers:
                result.notes.append(f"Adjuster {adjuster_name} will be created.")
    elif payer == PayerType.CUSTOMER_PAY and (insurer_name or adjuster_name or claim_number):
        errors.append("payer_type: a CUSTOMER_PAY row cannot have an insurer, adjuster or claim number.")

    estimate_text = text("original_estimate", "Original estimate", required=True)
    if estimate_text:
        try:
            clean["original_estimate_cents"] = dollars_to_cents(estimate_text)
        except ValueError:
            errors.append("original_estimate: dollars, 0 or more, with at most 2 decimals.")

    stage_text = values.get("stage", "").upper() or Stage.CHECKED_IN.value
    if stage_text not in Stage.__members__:
        errors.append(f"stage: {values.get('stage')} is not a stage code.")
    elif Stage(stage_text) in NOT_ALLOWED_STAGES:
        errors.append(f"stage: {stage_text} is not allowed in an import.")
    else:
        clean["stage"] = Stage(stage_text)

    consent_text = values.get("consent", "").upper() or "NO"
    if consent_text not in ("YES", "NO"):
        errors.append("consent: must be YES or NO.")
    clean["consent"] = consent_text == "YES"

    clean["location_id"] = None
    location_name = values.get("location", "")
    if location_name:
        location = db.scalar(
            select(Location).where(Location.shop_id == shop.id, Location.is_active.is_(True), func.lower(Location.name) == location_name.lower())
        )
        if location is None:
            errors.append(f"location: {location_name} is not one of this shop's active locations.")
        else:
            clean["location_id"] = location.id
    return result


def dry_run(db: Session, shop: Shop, data: bytes, create_missing_insurers: bool, now: dt.datetime) -> list[RowResult]:
    """Validate every row. Nothing is saved."""
    rows = read_rows(data)
    seen: set[str] = set()
    return [validate_row(db, shop, row_number, values, create_missing_insurers, now, seen) for row_number, values in rows]


# ---------------------------------------------------------------- commit


def commit_import(
    db: Session, shop: Shop, user: User, data: bytes, create_missing_insurers: bool, now: dt.datetime, default_location_id: int | None = None
) -> int:
    """Save every row in 1 transaction. Raises ImportFileError if the dry run has any error.

    Rows with an empty location go to `default_location_id` (the importer's location).
    """
    results = dry_run(db, shop, data, create_missing_insurers, now)
    if any(not r.ok for r in results):
        raise ImportFileError("The file has errors. Fix them and upload it again.")
    try:
        for result in results:
            result.clean["location_id"] = result.clean.get("location_id") or default_location_id
            _save_row(db, shop, user, result.clean, now)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return len(results)


def _save_row(db: Session, shop: Shop, user: User, clean: dict, now: dt.datetime) -> None:
    customer = db.scalar(select(Customer).where(Customer.shop_id == shop.id, Customer.phone_e164 == clean["phone_e164"]))
    if customer is None:
        customer = Customer(
            shop_id=shop.id, phone_e164=clean["phone_e164"], first_name=clean["first_name"], last_name=clean["last_name"], email=clean["email"]
        )
        db.add(customer)
        db.flush()

    insurer_id = adjuster_id = None
    if clean["payer_type"] == PayerType.INSURANCE:
        insurer = _find_insurer(db, shop.id, clean["insurer_name"])
        if insurer is None:
            insurer = Insurer(shop_id=shop.id, name=clean["insurer_name"], is_drp=False)
            db.add(insurer)
            db.flush()
        insurer_id = insurer.id
        if clean["adjuster_name"]:
            adjuster = _find_adjuster(db, shop.id, insurer.id, clean["adjuster_name"])
            if adjuster is None:
                adjuster = Adjuster(shop_id=shop.id, insurer_id=insurer.id, full_name=clean["adjuster_name"])
                db.add(adjuster)
                db.flush()
            adjuster_id = adjuster.id

    ro = RepairOrder(
        shop_id=shop.id,
        ro_number=clean["ro_number"],
        customer_id=customer.id,
        vehicle_year=clean["vehicle_year"],
        vehicle_make=clean["vehicle_make"],
        vehicle_model=clean["vehicle_model"],
        vehicle_color=clean["vehicle_color"],
        vin=clean["vin"],
        payer_type=clean["payer_type"],
        insurer_id=insurer_id,
        adjuster_id=adjuster_id,
        claim_number=clean["claim_number"] if clean["payer_type"] == PayerType.INSURANCE else None,
        original_estimate_cents=clean["original_estimate_cents"],
        current_stage=clean["stage"],
        checked_in_at=clean["checked_in_at"],
        location_id=clean.get("location_id"),
    )
    ensure_status_token(ro)
    db.add(ro)
    db.flush()
    db.add(
        StageEvent(
            shop_id=shop.id,
            repair_order_id=ro.id,
            from_stage=None,
            to_stage=clean["stage"],
            changed_by_user_id=user.id,
            changed_at=now,
            note=IMPORTED_NOTE,
        )
    )
    if clean["consent"]:
        db.add(
            Consent(
                shop_id=shop.id,
                customer_id=customer.id,
                phone_e164=customer.phone_e164,
                status=ConsentStatus.OPTED_IN,
                method=ConsentMethod.IMPORTED,
                recorded_by_user_id=user.id,
                recorded_at=now,
            )
        )
    db.flush()


# ---------------------------------------------------------------- holding the uploaded file


def save_upload(data: bytes, now: dt.datetime | None = None) -> str:
    IMPORT_TMP_DIR.mkdir(parents=True, exist_ok=True)
    upload_id = str(uuid.uuid4())
    path = IMPORT_TMP_DIR / f"{upload_id}.csv"
    path.write_bytes(data)
    stamp = (now or utcnow()).timestamp()
    os.utime(path, (stamp, stamp))  # age is measured with the app clock
    return upload_id


def upload_path(upload_id: str) -> Path | None:
    """The held file for a UUID from the commit form, or None if the id is not a UUID4 or the file is gone."""
    try:
        parsed = uuid.UUID(upload_id, version=4)
    except (ValueError, TypeError, AttributeError):
        return None
    if str(parsed) != upload_id:
        return None
    path = IMPORT_TMP_DIR / f"{upload_id}.csv"
    return path if path.is_file() else None


def delete_upload(upload_id: str) -> None:
    path = upload_path(upload_id)
    if path is not None:
        path.unlink(missing_ok=True)


def delete_old_uploads(now: dt.datetime | None = None) -> int:
    """Delete held files older than 1 hour."""
    if not IMPORT_TMP_DIR.is_dir():
        return 0
    cutoff = (now or utcnow()).timestamp() - UPLOAD_MAX_AGE_SECONDS
    removed = 0
    for path in IMPORT_TMP_DIR.glob("*.csv"):
        if path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
            removed += 1
    return removed
