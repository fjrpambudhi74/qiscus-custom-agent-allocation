import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.allocation import process_queue
from app.config import settings
from app.database import get_session

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def poll_and_drain_queue() -> None:
    """Safety-net job: Qiscus has no webhook for "agent came back online",
    so a customer queued because everyone was full/offline would otherwise
    stay stuck until some unrelated Mark As Resolved happened to fire. This
    retries the queue periodically to catch that case (and any lost
    webhook call). process_queue() checks the local queue table first and
    only spends API calls if someone's actually waiting, so this is a
    no-op (zero API calls) on every tick where the queue is empty."""
    session = next(get_session())
    try:
        process_queue(session)
    except Exception:
        logger.exception("Scheduled queue drain failed")
    finally:
        session.close()


def start_scheduler() -> None:
    scheduler.add_job(
        poll_and_drain_queue,
        "interval",
        seconds=settings.poll_interval_seconds,
        id="poll_and_drain_queue",
        replace_existing=True,
    )
    scheduler.start()


def stop_scheduler() -> None:
    scheduler.shutdown(wait=False)
