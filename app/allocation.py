import json
import logging
import threading
from datetime import datetime
from typing import Any, Optional

from sqlmodel import Session, select

from app.config import settings
from app.models import Assignment, AssignmentStatus, QueueEntry, QueueStatus
from app.qiscus_client import get_qiscus_client
from app.schemas import AllocationWebhookPayload, ResolvedWebhookPayload

logger = logging.getLogger(__name__)

# Serializes the "check current_customer_count, then decide, then call Assign
# Agent" critical section across every concurrent webhook/scheduler thread in
# this process. Necessary but NOT sufficient on its own - see
# get_available_agent_for_room()'s local-count fallback below for why.
# A plain threading.Lock is correct here because FastAPI runs these sync
# route handlers in a thread pool, and APScheduler's jobs run on their own
# thread too - all real concurrency in this single-replica deployment is
# thread-based, not multi-process.
_allocation_lock = threading.Lock()


def _local_active_count(session: Session, qiscus_agent_id: str) -> int:
    rows = session.exec(
        select(Assignment).where(
            Assignment.qiscus_agent_id == str(qiscus_agent_id),
            Assignment.status == AssignmentStatus.active,
        )
    ).all()
    return len(rows)


def get_available_agent_for_room(session: Session, room_id: str) -> Optional[dict[str, Any]]:
    """Ask Qiscus (live) who's eligible for this room, then apply our own
    configurable max-capacity rule - least-busy-under-cap wins.

    Capacity is the *max* of Qiscus's own `current_customer_count` and our
    own locally-committed active-assignment count for that agent, not
    Qiscus's number alone. Confirmed in production under load
    (2026-08-20): even with `_allocation_lock` correctly serializing our
    own requests (assignments happened one at a time, not concurrently),
    a room could still get assigned to an agent already at its cap,
    because `current_customer_count` hadn't caught up yet to an
    assignment we'd made moments earlier - Qiscus's own count lags behind
    its writes under load. Our local count is written synchronously
    inside this same lock, so it never has that lag - it's the
    authoritative floor, with Qiscus's count only able to push a
    candidate's effective count *up* (e.g. a manual dashboard assignment
    we don't know about locally), never down.
    """
    agents = get_qiscus_client().available_agents(room_id)
    candidates = []
    for raw in agents:
        agent_id = raw.get("id")
        if agent_id is None:
            continue
        is_available = raw.get("is_available", True)
        qiscus_count = raw.get("current_customer_count", 0)
        count = max(qiscus_count, _local_active_count(session, agent_id))
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


def _existing_active_assignment(session: Session, room_id: str) -> Optional[Assignment]:
    return session.exec(
        select(Assignment).where(
            Assignment.room_id == room_id,
            Assignment.status == AssignmentStatus.active,
        )
    ).first()


def _existing_waiting_queue_entry(session: Session, room_id: str) -> Optional[QueueEntry]:
    return session.exec(
        select(QueueEntry).where(
            QueueEntry.room_id == room_id,
            QueueEntry.status == QueueStatus.waiting,
        )
    ).first()


def handle_new_session(session: Session, payload: AllocationWebhookPayload) -> None:
    # Qiscus doesn't send a distinct customer id field here - email is the
    # closest stable identifier in the real payload shape.
    customer_id = payload.email

    with _allocation_lock:
        # Qiscus redelivers the allocation webhook for the same room under
        # load (confirmed in production, 2026-08-20 - every room in a
        # concurrent test got the webhook twice). Without this check a
        # redelivery re-runs the whole decision and can double-assign.
        existing = _existing_active_assignment(session, payload.room_id)
        if existing:
            logger.info(
                "Ignoring duplicate allocation webhook for room=%s (already assigned to agent=%s)",
                payload.room_id, existing.qiscus_agent_id,
            )
            return

        candidate = get_available_agent_for_room(session, payload.room_id)
        if candidate:
            assign_customer_to_agent(session, candidate["id"], payload.room_id, customer_id)
        elif _existing_waiting_queue_entry(session, payload.room_id):
            # Same redelivery scenario, but caught here instead: the first
            # delivery already queued this room (nobody was free yet).
            # Without this check, a redelivered webhook creates a *second*
            # queue entry for the same room - observed in production
            # (2026-08-20): the leftover duplicate entry survived long
            # enough that by the time the scheduler got to it, the room had
            # already been served *and resolved* through the original
            # entry, so `_existing_active_assignment` above no longer
            # caught it (status had moved past "active"), and Assign Agent
            # correctly rejected it ("room already resolved") - harmless
            # (auto-marked `failed` by process_queue), but a wasted API
            # call and a confusing error log for something that was never
            # a real customer needing service.
            logger.info(
                "Room=%s is already queued (duplicate allocation webhook) - not adding a second entry",
                payload.room_id,
            )
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

        with _allocation_lock:
            # Same duplicate-webhook scenario as handle_new_session can
            # land a room in the queue that's already been served by
            # another (non-duplicate) delivery - discard rather than
            # trying to assign it a second time.
            existing = _existing_active_assignment(session, entry.room_id)
            if existing:
                logger.info(
                    "Queue entry id=%s room=%s already assigned to agent=%s - discarding duplicate",
                    entry.id, entry.room_id, existing.qiscus_agent_id,
                )
                entry.status = QueueStatus.assigned
                session.add(entry)
                session.commit()
                continue

            candidate = get_available_agent_for_room(session, entry.room_id)
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
