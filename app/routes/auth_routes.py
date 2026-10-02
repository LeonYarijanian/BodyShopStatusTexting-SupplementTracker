"""Login and logout."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import attempt_login, get_db, start_session
from app.business_days import utcnow
from app.routes import render

router = APIRouter()


@router.get("/login")
def login_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse("/", status_code=302)
    return render(request, "login.html", email="", error=None)


@router.post("/login")
def login_submit(request: Request, email: str = Form(""), password: str = Form(""), db: Session = Depends(get_db)):
    now = utcnow()
    user, error = attempt_login(db, email, password, now)
    if user is None:
        return render(request, "login.html", email=email, error=error)
    start_session(request, user, now)
    return RedirectResponse("/", status_code=302)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)
