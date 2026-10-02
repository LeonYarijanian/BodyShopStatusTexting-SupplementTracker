"""Settings (ADMIN only)."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_db, require_admin
from app.routes import render

router = APIRouter()


@router.get("/settings")
def settings_page(request: Request, current: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    return render(request, "settings.html", db, current)
