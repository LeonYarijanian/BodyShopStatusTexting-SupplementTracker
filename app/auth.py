"""Login, sessions, password hashing, CSRF and role checks (Sections 9 and 12)."""

import datetime as dt
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.business_days import utcnow
from app.enums import Role
from app.models import LoginAttempt, Shop, User

MIN_PASSWORD_LENGTH = 12
LOCKOUT_FAILURES = 10
LOCKOUT_WINDOW = dt.timedelta(minutes=15)
LOCKOUT_DURATION = dt.timedelta(minutes=15)
SESSION_IDLE_LIMIT = dt.timedelta(hours=12)

WRONG_LOGIN_MESSAGE = "Email or password is incorrect."
LOCKED_MESSAGE = "Too many attempts. Try again in 15 minutes."

_hasher = PasswordHasher()  # argon2id by default


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def validate_new_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")


# ---------------------------------------------------------------- login


def is_locked_out(db: Session, email: str, now: dt.datetime) -> bool:
    """10 failures within 15 minutes lock the email for 15 minutes after the 10th failure."""
    since = now - LOCKOUT_WINDOW - LOCKOUT_DURATION
    failures = db.scalars(
        select(LoginAttempt.attempted_at)
        .where(LoginAttempt.email == email, LoginAttempt.succeeded.is_(False), LoginAttempt.attempted_at > since)
        .order_by(LoginAttempt.attempted_at, LoginAttempt.id)
    ).all()
    for i in range(LOCKOUT_FAILURES - 1, len(failures)):
        if failures[i] - failures[i - LOCKOUT_FAILURES + 1] <= LOCKOUT_WINDOW and now < failures[i] + LOCKOUT_DURATION:
            return True
    return False


def attempt_login(db: Session, email: str, password: str, now: dt.datetime) -> tuple[User | None, str | None]:
    """Returns (user, None) on success or (None, error message)."""
    email = (email or "").strip().lower()
    if is_locked_out(db, email, now):
        return None, LOCKED_MESSAGE
    user = db.scalar(select(User).where(User.email == email))
    ok = user is not None and user.is_active and verify_password(user.password_hash, password or "")
    db.add(LoginAttempt(email=email, attempted_at=now, succeeded=ok))
    db.commit()
    if not ok:
        return None, WRONG_LOGIN_MESSAGE
    return user, None


# ---------------------------------------------------------------- sessions and CSRF


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


async def csrf_protect(request: Request) -> None:
    """Every POST carries the per-session token, as a form field or the X-CSRF-Token header."""
    if request.method != "POST" or request.url.path.startswith("/webhooks/"):
        return
    expected = request.session.get("csrf")
    supplied = request.headers.get("X-CSRF-Token")
    if not supplied:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            form = await request.form()
            supplied = form.get("csrf_token")
    if not expected or not supplied or not secrets.compare_digest(str(expected), str(supplied)):
        raise HTTPException(status_code=403, detail="Missing or invalid CSRF token.")


def start_session(request: Request, user: User, now: dt.datetime) -> None:
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["last_seen"] = now.timestamp()
    request.session["csrf"] = secrets.token_urlsafe(32)


class LoginRequired(Exception):
    pass


def get_db(request: Request):
    db = request.app.state.SessionLocal()
    try:
        yield db
    finally:
        db.close()


class CurrentUser:
    """The logged-in user plus their shop. Every query filters by `shop_id`."""

    def __init__(self, user: User, shop: Shop):
        self.user = user
        self.shop = shop

    @property
    def id(self) -> int:
        return self.user.id

    @property
    def shop_id(self) -> int:
        return self.shop.id

    @property
    def is_admin(self) -> bool:
        return self.user.role == Role.ADMIN


def require_user(request: Request, db: Session = Depends(get_db)) -> CurrentUser:
    user_id = request.session.get("user_id")
    last_seen = request.session.get("last_seen")
    now = utcnow()
    if not user_id or last_seen is None or now.timestamp() - float(last_seen) > SESSION_IDLE_LIMIT.total_seconds():
        request.session.clear()
        raise LoginRequired()
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        request.session.clear()
        raise LoginRequired()
    request.session["last_seen"] = now.timestamp()
    shop = db.get(Shop, user.shop_id)
    return CurrentUser(user, shop)


def require_admin(current: CurrentUser = Depends(require_user)) -> CurrentUser:
    if not current.is_admin:
        raise HTTPException(status_code=403, detail="Admins only.")
    return current


def get_owned(db: Session, model, record_id: int, shop_id: int):
    """Load a record of the user's shop. A record from another shop returns 404, never 403."""
    record = db.get(model, record_id)
    if record is None or record.shop_id != shop_id:
        raise HTTPException(status_code=404, detail="Not found.")
    return record
