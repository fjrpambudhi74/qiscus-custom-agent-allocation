import json
import logging
from datetime import datetime
from typing import Any, Optional

from sqlmodel import Session, select

from app.config import settings
from app.models import Assignment, AssignmentStatus, QueueEntry, QueueStatus
from app.qiscus_client import get_qiscus_client
from app.schemas import AllocationWebhookPayload, ResolvedWebhookPayload

logger = logging.getLogger(__name__)


def get_available_agent_for_room(room_id: str) -> Optional[dict[str, Any]]:
    """Ask Qiscus (live) who's eligible for this room, then apply our own
    configurable max-capacity rule on top of Qiscus's own
    `current_customer_count` - least-busy-under-cap wins.

    No local session needed: this doesn't touch our DB, it's a live read
    from Available Agents (v2), which is why we don't need to track
    per-agent online status/capacity locally anymore (see qiscus_client.py
    for why this endpoint was chosen over Get All Agents).
    """
    agents = get_qiscus_client().available_agents(room_id)
    candidates = []
    for raw in agents:
        agent_id = raw.get("id")
        if agent_id is None:
            continue
        is_available = raw.get("is_available", True)
        count = raw.get("current_customer_count", 0)
        if is_available and count < settings.default_max_capacity:
            candidates.append((raw, count))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[1])
    return candidates[0][0]


def assign_customer_to_agent(
    session: Session, qiscus_agent_id: str, room_id: str, customer_id: Optional[str]
) -> bool:
    ok = get_qiscus_client().assign_agent(room_id=room_id, agent_id=str(qiscus_agent_id))
    if not ok:
        logger.error("Assign API call failed for room=%s agent=%s", room_id, qiscus_agent_id)
        return False
    session.add(
        Assignment(
            room_id=room_id,
            customer_id=customer_id,
            qiscus_agent_id=str(qiscus_agent_id),
            status=AssignmentStatus.active,
        )
    )
    session.commit()
    logger.info("Assigned room=%s to agent=%s", room_id, qiscus_agent_id)
    return True


def enqueue_customer(session: Session, room_id: str, customer_id: Optional[str], raw_payload: dict) -> None:
    session.add(
        QueueEntry(
            room_id=room_id,
            customer_id=customer_id,
            raw_payload=json.dumps(raw_payload),
            status=QueueStatus.waiting,
        )
    )
    session.commit()
    logger.info("Queued room=%s (no agent available)", room_id)


def handle_new_session(session: Session, payload: AllocationWebhookPayload) -> None:
    # Qiscus doesn't send a distinct customer id field here - email is the
    # closest stable identifier in the real payload shape.
    customer_id = payload.email

    candidate = get_available_agent_for_room(payload.room_id)
    if candidate:
        assign_customer_to_agent(session, candidate["id"], payload.room_id, customer_id)
    else:
        enqueue_customer(session, payload.room_id, customer_id, payload.model_dump())


def handle_resolved(session: Session, payload: ResolvedWebhookPayload) -> None:
    assignment = session.exec(
        select(Assignment).where(
            Assignment.room_id == payload.room_id,
            Assignment.status == AssignmentStatus.active,
        )
    ).first()
    if assignment:
        assignment.status = AssignmentStatus.resolved
        assignment.resolved_at = datetime.utcnow()
        session.add(assignment)
        session.commit()
        logger.info("Resolved room=%s (agent=%s freed)", payload.room_id, assignment.qiscus_agent_id)
    else:
        logger.warning("Resolved webhook for unknown/inactive room=%s", payload.room_id)

    process_queue(session)


def process_queue(session: Session) -> None:
    """Drain the FIFO queue, oldest first.

    Checks the local queue table *before* ever calling the API - if it's
    empty (the common case), this is a pure local read with zero API calls.
    Only once we know there's someone waiting do we spend an API call
    checking if their specific room now has a free agent.

    If the oldest entry has no candidate agent yet (everyone's still full),
    we stop rather than skipping ahead - that's likely transient and keeps
    FIFO ordering strict. But if the Assign Agent call itself fails (a real
    API error, not just "nobody free"), retrying the same request forever
    would both spam identical errors and permanently block every customer
    queued behind it - a single bad room_id must not stall the whole
    queue - so that entry is marked `failed` and we move on to the next one
    instead of stopping.
    """
    while True:
        entry = session.exec(
            select(QueueEntry)
            .where(QueueEntry.status == QueueStatus.waiting)
            .order_by(QueueEntry.created_at.asc())
        ).first()
        if not entry:
            break

        candidate = get_available_agent_for_room(entry.room_id)
        if not candidate:
            break

        assigned = assign_customer_to_agent(session, candidate["id"], entry.room_id, entry.customer_id)
        if not assigned:
            logger.warning(
                "Giving up on queue entry id=%s room=%s after a failed assign call - "
                "marking failed instead of retrying forever",
                entry.id, entry.room_id,
            )
            entry.status = QueueStatus.failed
            session.add(entry)
            session.commit()
            continue

        entry.status = QueueStatus.assigned
        session.add(entry)
        session.commit()
