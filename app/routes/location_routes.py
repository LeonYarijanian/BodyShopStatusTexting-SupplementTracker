"""The location switcher in the top bar (Section 16 item 9)."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from app.auth import CurrentUser, require_user

router = APIRouter()


@router.post("/location")
async def switch_location(request: Request, current: CurrentUser = Depends(require_user)):
    """Remember the chosen location for this session. "all" (or an unknown id) shows every location."""
    form = await request.form()
    chosen = (form.get("location_id") or "").strip()
    ids = {location.id for location in current.locations}
    request.session["location_id"] = int(chosen) if chosen.isdigit() and int(chosen) in ids else 0
    target = (form.get("next") or "").strip()
    if not target.startswith("/") or target.startswith("//"):
        target = "/"
    return RedirectResponse(target, status_code=303)
