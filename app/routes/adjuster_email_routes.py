"""AI-drafted adjuster follow-up emails: draft, edit, approve and send (Section 16 item 7)."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.ai_drafts import DraftError, approve_and_send, create_draft, discard, open_draft, save_edits, supplement_facts
from app.auth import CurrentUser, get_db, get_owned, require_user
from app.business_days import utcnow
from app.enums import AdjusterEmailStatus
from app.models import AdjusterEmail, Supplement
from app.routes import render

router = APIRouter()


@router.post("/supplements/{supplement_id}/email-draft")
async def new_email_draft(supplement_id: int, request: Request, current: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    supplement = get_owned(db, Supplement, supplement_id, current.shop_id)
    existing = open_draft(db, supplement.id)
    if existing is not None:
        return RedirectResponse(f"/email-drafts/{existing.id}", status_code=303)
    try:
        email = create_draft(db, supplement, current.user, utcnow(), request.app.state.settings)
        db.commit()
    except DraftError as exc:
        db.rollback()
        request.session["flash"] = {"kind": "error", "message": str(exc)}
        return RedirectResponse(f"/ro/{supplement.repair_order_id}", status_code=303)
    return RedirectResponse(f"/email-drafts/{email.id}", status_code=303)


def _page(request: Request, db: Session, current: CurrentUser, email: AdjusterEmail, error: str | None = None, status_code: int = 200):
    supplement = email.supplement
    facts = supplement_facts(db, supplement, current.user, utcnow())
    return render(request, "email_draft.html", db, current, status_code=status_code, email=email, supplement=supplement, facts=facts, error=error)


@router.get("/email-drafts/{email_id}")
def email_draft_page(email_id: int, request: Request, current: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    email = get_owned(db, AdjusterEmail, email_id, current.shop_id)
    return _page(request, db, current, email)


@router.post("/email-drafts/{email_id}")
async def email_draft_action(email_id: int, request: Request, current: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    email = get_owned(db, AdjusterEmail, email_id, current.shop_id)
    form = await request.form()
    action = (form.get("action") or "save").strip()
    now = utcnow()
    settings = request.app.state.settings
    try:
        if action == "discard":
            discard(email)
            db.commit()
            request.session["flash"] = {"kind": "notice", "message": "Draft discarded. Nothing was sent."}
            return RedirectResponse(f"/ro/{email.supplement.repair_order_id}", status_code=303)
        if action == "redraft":
            discard(email)
            fresh = create_draft(db, email.supplement, current.user, now, settings)
            db.commit()
            return RedirectResponse(f"/email-drafts/{fresh.id}", status_code=303)
        save_edits(email, form.get("to_email") or "", form.get("subject") or "", form.get("body") or "")
        if action == "send":
            approve_and_send(db, email, current.user, now, settings)
            db.commit()
            request.session["flash"] = {"kind": "notice", "message": f"Sent to {email.to_email} and logged as a follow-up."}
            return RedirectResponse(f"/ro/{email.supplement.repair_order_id}", status_code=303)
        db.commit()
        request.session["flash"] = {"kind": "notice", "message": "Draft saved. It has not been sent."}
        return RedirectResponse(f"/email-drafts/{email.id}", status_code=303)
    except DraftError as exc:
        if email.status == AdjusterEmailStatus.FAILED:
            db.commit()
        else:
            db.rollback()
        db.refresh(email)
        return _page(request, db, current, email, error=str(exc), status_code=422)
