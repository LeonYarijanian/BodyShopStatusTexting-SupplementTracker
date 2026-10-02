"""Reports page (Section 10)."""

import datetime as dt

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_db, require_user
from app.business_days import utcnow
from app.reports import all_reports, default_range
from app.routes import render

router = APIRouter()


def _parse(text: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(text) if text else None
    except ValueError:
        return None


@router.get("/reports")
def reports_page(request: Request, date_from: str = "", date_to: str = "", current: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    now = utcnow()
    default_from, default_to = default_range(now, current.shop.timezone)
    start = _parse(date_from) or default_from
    end = _parse(date_to) or default_to
    error = None
    if end < start:
        error = "The 'to' date must be on or after the 'from' date."
        start, end = default_from, default_to
    return render(
        request,
        "reports.html",
        db,
        current,
        date_from=start.isoformat(),
        date_to=end.isoformat(),
        range_error=error,
        reports=all_reports(db, current.shop_id, start, end, now, current.location_id),
    )
