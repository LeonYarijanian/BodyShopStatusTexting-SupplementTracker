"""CSV import (ADMIN only): upload, dry run, then commit."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_db, require_admin
from app.business_days import utcnow
from app.csv_import import (
    COLUMNS,
    MAX_BYTES,
    ImportFileError,
    commit_import,
    delete_old_uploads,
    delete_upload,
    dry_run,
    save_upload,
    upload_path,
)
from app.routes import render

router = APIRouter()


def _page(request: Request, db: Session, current: CurrentUser, status_code: int = 200, **context):
    context.setdefault("results", None)
    context.setdefault("file_error", None)
    context.setdefault("upload_id", None)
    context.setdefault("create_missing", False)
    context.setdefault("committed", None)
    return render(request, "import.html", db, current, status_code=status_code, columns=COLUMNS, **context)


@router.get("/import")
def import_page(request: Request, current: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    delete_old_uploads(utcnow())
    return _page(request, db, current)


@router.post("/import")
async def import_submit(request: Request, current: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    now = utcnow()
    delete_old_uploads(now)
    form = await request.form()
    action = (form.get("action") or "dry_run").strip()
    create_missing = form.get("create_missing_insurers") in ("yes", "on", "true", "1")

    if action == "commit":
        upload_id = (form.get("upload_id") or "").strip()
        path = upload_path(upload_id)
        if path is None:
            return _page(request, db, current, status_code=400, file_error="The uploaded file has expired. Upload it again.")
        data = path.read_bytes()
        try:
            count = commit_import(db, current.shop, current.user, data, create_missing, now)
        except ImportFileError as exc:
            results = None
            try:
                results = dry_run(db, current.shop, data, create_missing, now)
            except ImportFileError:
                pass
            return _page(request, db, current, status_code=400, file_error=str(exc), results=results, upload_id=upload_id, create_missing=create_missing)
        delete_upload(upload_id)
        return _page(request, db, current, committed=count)

    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        return _page(request, db, current, status_code=422, file_error="Choose a CSV file to upload.")
    data = await upload.read(MAX_BYTES + 1)
    try:
        results = dry_run(db, current.shop, data, create_missing, now)
    except ImportFileError as exc:
        return _page(request, db, current, status_code=422, file_error=str(exc))
    upload_id = save_upload(data, now)
    return _page(request, db, current, results=results, upload_id=upload_id, create_missing=create_missing)
