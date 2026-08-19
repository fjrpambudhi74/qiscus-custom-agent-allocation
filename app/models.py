from datetime import datetime
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


class AssignmentStatus(str, Enum):
    active = "active"
    resolved = "resolved"


class QueueStatus(str, Enum):
    waiting = "waiting"
    assigned = "assigned"


class Assignment(SQLModel, table=True):
    """Audit/history record - who got assigned where. Not used for capacity
    math anymore; capacity comes live from the Available Agents (v2) API's
    `current_customer_count` (see qiscus_client.py) rather than a local
    count, so this table can't drift out of sync with reality."""

    id: Optional[int] = Field(default=None, primary_key=True)
    room_id: str = Field(index=True)
    customer_id: Optional[str] = None
    qiscus_agent_id: str = Field(index=True)
    status: AssignmentStatus = AssignmentStatus.active
    assigned_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = None


class QueueEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    room_id: str = Field(index=True)
    customer_id: Optional[str] = None
    raw_payload: Optional[str] = None
    status: QueueStatus = QueueStatus.waiting
    created_at: datetime = Field(default_factory=datetime.utcnow)
