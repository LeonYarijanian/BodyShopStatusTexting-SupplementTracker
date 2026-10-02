"""Multiple locations per shop (Section 16 item 9).

Locations are optional. A shop with fewer than 2 active locations works like v1: no location switcher,
nothing is filtered, and texts use the shop's phone number and Twilio number.

When an admin adds the first extra location, a "Main" location is made from the shop's own phone and
address, and every existing RO is put there. Main keeps texting from the shop's Twilio number. From then on every RO belongs to a location:
- the board, the supplements page, the top-bar counters and the reports show the location picked in the
  location switcher ("All locations" shows everything);
- texts about an RO show its location's phone number and go out from its location's Twilio number (when it
  has one; otherwise from the shop's number);
- a user's home location is what the switcher starts on. It is a default view, not a permission: anyone can
  switch to another location.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.enums import ACTIVE_STAGES
from app.messaging.templates import format_us_phone, parse_us_phone
from app.models import Location, RepairOrder, Shop, ShopSettings, User

MAIN_LOCATION_NAME = "Main"
MAX_NAME = 120
MAX_ADDRESS = 200


class LocationError(ValueError):
    pass


def shop_locations(db: Session, shop_id: int, active_only: bool = True) -> list[Location]:
    query = select(Location).where(Location.shop_id == shop_id)
    if active_only:
        query = query.where(Location.is_active.is_(True))
    return list(db.scalars(query.order_by(Location.id)).all())


def active_location_id(chosen: int | None, home: int | None, locations: list[Location]) -> int | None:
    """The location to filter by: the switcher's choice, else the user's home location. None means all.

    `chosen` is what the switcher stored: a location id, 0 for "All locations", or None when never used.
    Nothing is filtered unless the shop has at least 2 active locations.
    """
    if len(locations) < 2:
        return None
    ids = {location.id for location in locations}
    wanted = home if chosen is None else chosen
    return wanted if wanted in ids else None


def default_location_id(db: Session, shop_id: int, filtered: int | None, home: int | None = None) -> int | None:
    """Where a new RO goes when nobody picked: the filtered location, the user's home, else the first active one."""
    locations = shop_locations(db, shop_id)
    ids = [location.id for location in locations]
    for candidate in (filtered, home):
        if candidate in ids:
            return candidate
    return ids[0] if ids else None


def phone_for(shop: Shop, location: Location | None) -> str:
    return location.phone_e164 if location is not None else shop.phone_e164


def address_for(shop: Shop, location: Location | None) -> str | None:
    return location.address if location is not None else shop.address


def review_url_for(settings: ShopSettings, location: Location | None) -> str:
    return (location.review_url if location is not None else "") or settings.review_url


def ro_phone(shop: Shop, ro: RepairOrder) -> str:
    """The phone number customers and adjusters should call about this RO."""
    return phone_for(shop, ro.location)


def location_for_number(db: Session, shop_id: int, number: str) -> Location | None:
    """The location a customer texted: its Twilio number, or its phone number in DEMO mode."""
    if not number:
        return None
    for column in (Location.twilio_from_e164, Location.phone_e164):
        found = db.scalar(select(Location).where(Location.shop_id == shop_id, column == number).order_by(Location.id).limit(1))
        if found is not None:
            return found
    return None


def scope_ros(query, location_id: int | None):
    """Add the location filter to a query that selects from or joins repair_orders."""
    if location_id is None:
        return query
    return query.where(RepairOrder.location_id == location_id)


# ---------------------------------------------------------------- admin actions


def ensure_main_location(db: Session, shop: Shop) -> Location:
    """The shop's first location, made from the shop's own details if there is none. ROs without a location go there.

    Its Twilio number stays empty, so its texts keep going out exactly as before (the shop's number, or the
    messaging service's choice).
    """
    first = db.scalar(select(Location).where(Location.shop_id == shop.id).order_by(Location.id).limit(1))
    if first is None:
        first = Location(shop_id=shop.id, name=MAIN_LOCATION_NAME, phone_e164=shop.phone_e164, address=shop.address, twilio_from_e164="", is_active=True)
        db.add(first)
        db.flush()
    for ro in db.scalars(select(RepairOrder).where(RepairOrder.shop_id == shop.id, RepairOrder.location_id.is_(None))).all():
        ro.location_id = first.id
    db.flush()
    return first


def _clean(db: Session, shop: Shop, values: dict, location: Location | None) -> dict:
    name = (values.get("name") or "").strip()
    if not 1 <= len(name) <= MAX_NAME:
        raise LocationError(f"Location name must be 1 to {MAX_NAME} characters.")
    same_name = select(Location.id).where(Location.shop_id == shop.id, func.lower(Location.name) == name.lower())
    if location is not None:
        same_name = same_name.where(Location.id != location.id)
    if db.scalar(same_name) is not None:
        raise LocationError(f"There is already a location named {name}.")
    try:
        phone = parse_us_phone((values.get("phone") or "").strip())
    except ValueError:
        raise LocationError("Location phone must be a valid US phone number.") from None
    address = (values.get("address") or "").strip()
    if len(address) > MAX_ADDRESS:
        raise LocationError(f"Location address must be at most {MAX_ADDRESS} characters.")
    review_url = (values.get("review_url") or "").strip()
    if review_url and (not review_url.startswith("https://") or len(review_url) > 500):
        raise LocationError("The location's review URL must be empty or start with https:// (at most 500 characters).")
    twilio = (values.get("twilio_from_e164") or "").strip()
    if twilio:
        try:
            if parse_us_phone(twilio) != twilio:
                raise ValueError
        except ValueError:
            raise LocationError("The location's Twilio number must be empty or a valid E.164 number, such as +18185550188.") from None
        taken = select(Location.id).where(Location.twilio_from_e164 == twilio, Location.shop_id != shop.id)
        other_shop = select(ShopSettings.id).where(ShopSettings.twilio_from_e164 == twilio, ShopSettings.shop_id != shop.id)
        same_shop = select(Location.id).where(Location.shop_id == shop.id, Location.twilio_from_e164 == twilio)
        if location is not None:
            same_shop = same_shop.where(Location.id != location.id)
        if db.scalar(taken) is not None or db.scalar(other_shop) is not None or db.scalar(same_shop) is not None:
            raise LocationError("That Twilio number is already used by another location. Each location needs its own number.")
        own = db.scalar(select(ShopSettings.twilio_from_e164).where(ShopSettings.shop_id == shop.id))
        if own == twilio:
            raise LocationError("That is the shop's own Twilio number (Settings > Mode), which Main uses. Leave it empty to use it.")
    return {"name": name, "phone_e164": phone, "address": address or None, "twilio_from_e164": twilio, "review_url": review_url}


def add_location(db: Session, shop: Shop, values: dict) -> Location:
    clean = _clean(db, shop, values, None)
    ensure_main_location(db, shop)
    location = Location(shop_id=shop.id, is_active=True, **clean)
    db.add(location)
    db.flush()
    return location


def update_location(db: Session, shop: Shop, location: Location, values: dict) -> None:
    for field, value in _clean(db, shop, values, location).items():
        setattr(location, field, value)
    db.flush()


def open_ro_count(db: Session, location: Location) -> int:
    return int(
        db.scalar(
            select(func.count(RepairOrder.id)).where(RepairOrder.location_id == location.id, RepairOrder.current_stage.in_(ACTIVE_STAGES))
        )
        or 0
    )


def set_active(db: Session, location: Location, active: bool) -> None:
    """Close or reopen a location. Its delivered and cancelled ROs stay in the reports."""
    if active:
        location.is_active = True
        return
    if not location.is_active:
        return
    if len(shop_locations(db, location.shop_id)) <= 1:
        raise LocationError("A shop needs at least one active location.")
    open_count = open_ro_count(db, location)
    if open_count:
        raise LocationError(f"{location.name} has {open_count} open RO{'s' if open_count != 1 else ''}. Move them to another location first.")
    location.is_active = False
    for user in db.scalars(select(User).where(User.location_id == location.id)).all():
        user.location_id = None
    db.flush()


def set_home_location(db: Session, user: User, location_id: int | None) -> None:
    if location_id is not None:
        location = db.get(Location, location_id)
        if location is None or location.shop_id != user.shop_id or not location.is_active:
            raise LocationError("Choose one of this shop's active locations.")
    user.location_id = location_id


def location_label(location: Location) -> str:
    return f"{location.name} · {format_us_phone(location.phone_e164)}"
