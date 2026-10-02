"""Repair orders: new RO form, RO detail, stage changes."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_db, get_owned, require_user
from app.enums import ACTIVE_STAGES
from app.models import RepairOrder
from app.routes import render

router = APIRouter()


def needs_reply_count(db: Session, shop_id: int) -> int:
    """Active ROs with needs_reply = true."""
    return int(
        db.scalar(
            select(func.count(RepairOrder.id)).where(
                RepairOrder.shop_id == shop_id,
                RepairOrder.needs_reply.is_(True),
                RepairOrder.current_stage.in_(ACTIVE_STAGES),
            )
        )
        or 0
    )


@router.get("/ro/{ro_id}")
def ro_detail(ro_id: int, request: Request, current: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    ro = get_owned(db, RepairOrder, ro_id, current.shop_id)
    return render(request, "ro_detail.html", db, current, ro=ro)
