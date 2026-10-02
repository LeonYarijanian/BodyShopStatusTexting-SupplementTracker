"""Delete a customer's personal data on request (ADMIN only)."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_db, get_owned, require_admin
from app.business_days import utcnow
from app.models import Customer, RepairOrder
from app.privacy import anonymize_customer

router = APIRouter()
CONFIRM_WORD = "DELETE"


@router.post("/customers/{customer_id}/anonymize")
async def anonymize_route(customer_id: int, request: Request, current: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    customer = get_owned(db, Customer, customer_id, current.shop_id)
    form = await request.form()
    back = db.scalar(select(RepairOrder.id).where(RepairOrder.customer_id == customer.id).order_by(RepairOrder.id.desc()))
    target = f"/ro/{back}" if back else "/"
    if (form.get("confirm") or "").strip() != CONFIRM_WORD:
        request.session["flash"] = {"kind": "error", "message": f"Type {CONFIRM_WORD} to confirm. Nothing was deleted."}
        return RedirectResponse(target, status_code=303)
    result = anonymize_customer(db, customer, utcnow())
    db.commit()
    if result.get("already"):
        message = "This customer's data was already deleted."
    else:
        message = (
            f"Deleted the customer's personal data: {result['messages']} message(s) cleared, "
            f"{result['consents']} consent record(s) removed, {result['repair_orders']} RO(s) kept for reports."
        )
    request.session["flash"] = {"kind": "notice", "message": message}
    return RedirectResponse(target, status_code=303)
