"""Job Board (GET /)."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_db, require_user
from app.routes import render

router = APIRouter()


@router.get("/")
def board_page(request: Request, current: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    return render(request, "board.html", db, current)
