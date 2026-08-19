import logging

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.allocation import handle_new_session, handle_resolved
from app.database import get_session
from app.schemas import AllocationWebhookPayload, ResolvedWebhookPayload

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhook", tags=["webhooks"])


@router.post("/allocation")
def allocation_webhook(
    payload: AllocationWebhookPayload,
    session: Session = Depends(get_session),
) -> dict:
    logger.info("Allocation webhook received for room=%s", payload.room_id)
    handle_new_session(session, payload)
    return {"status": "received"}


@router.post("/resolved")
def resolved_webhook(
    payload: ResolvedWebhookPayload,
    session: Session = Depends(get_session),
) -> dict:
    logger.info("Resolved webhook received for room=%s", payload.room_id)
    handle_resolved(session, payload)
    return {"status": "received"}
