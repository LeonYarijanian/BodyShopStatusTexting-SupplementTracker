"""The 60-second job: run the sender and clean up old CSV uploads."""

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.business_days import utcnow

log = logging.getLogger(__name__)


def run_job(session_factory) -> None:
    from app.csv_import import delete_old_uploads
    from app.digest import run_digests
    from app.media import delete_old_media
    from app.messaging.engine import run_sender

    for name, step in (("Sender", run_sender), ("Digest", run_digests), ("Photo cleanup", delete_old_media)):
        db = session_factory()
        try:
            step(utcnow(), db)
        except Exception:  # keep the scheduler alive; the next run retries
            log.exception("%s job failed", name)
            db.rollback()
        finally:
            db.close()
    try:
        delete_old_uploads(utcnow())
    except Exception:
        log.exception("Upload cleanup failed")


def start_scheduler(app) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(run_job, "interval", seconds=60, args=[app.state.SessionLocal], id="sender", max_instances=1, coalesce=True)
    scheduler.start()
    return scheduler
