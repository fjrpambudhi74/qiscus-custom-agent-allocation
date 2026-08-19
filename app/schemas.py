from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class LatestService(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: Optional[int] = None
    room_id: Optional[str] = None
    is_resolved: Optional[bool] = None
    resolved_at: Optional[str] = None
    first_comment_id: Optional[str] = None
    last_comment_id: Optional[str] = None


class CandidateAgent(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: Optional[int] = None
    name: Optional[str] = None
    email: Optional[str] = None
    is_available: Optional[bool] = None
    assigned_rules: Optional[List[str]] = None


class AllocationWebhookPayload(BaseModel):
    """Payload Qiscus sends to the Custom Agent Allocation webhook.

    Confirmed against documentation.qiscus.com/omnichannel-chat/customization
    (2026-08-18) - real sample payload. Flat structure: customer fields
    (name/email/avatar_url) sit at the top level, not nested under a
    "customer" key. candidate_agent is a SINGLE object - Qiscus's own
    suggestion (generally its least-busy agent) - not a list. We still apply
    our own max-capacity/online rules on top rather than trusting it blindly,
    since it reflects Qiscus's default algorithm, not our custom one.
    """

    model_config = ConfigDict(extra="allow")

    app_id: Optional[str] = None
    source: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    avatar_url: Optional[str] = None
    is_resolved: Optional[bool] = None
    latest_service: Optional[LatestService] = None
    room_id: str
    candidate_agent: Optional[CandidateAgent] = None


class ResolvedChannel(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: Optional[int] = None
    name: Optional[str] = None
    source: Optional[str] = None


class ResolvedCustomer(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: Optional[str] = None
    user_id: Optional[str] = None
    avatar: Optional[str] = None


class ResolvedBy(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: Optional[int] = None
    name: Optional[str] = None
    email: Optional[str] = None
    type: Optional[str] = None


class ResolvedService(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: Optional[int] = None
    room_id: str
    is_resolved: Optional[bool] = None
    notes: Optional[str] = None
    source: Optional[str] = None


class ResolvedWebhookPayload(BaseModel):
    """Payload for the 'Mark As Resolved' webhook.

    Confirmed against documentation.qiscus.com/omnichannel-chat/webhook
    (2026-08-18) - room_id lives nested under `service`, not at the top
    level (an earlier, wrong guess had it flat - fixed here).
    """

    model_config = ConfigDict(extra="allow")

    app_code: Optional[str] = None
    channel: Optional[ResolvedChannel] = None
    customer: Optional[ResolvedCustomer] = None
    resolved_by: Optional[ResolvedBy] = None
    service: ResolvedService

    @property
    def room_id(self) -> str:
        return self.service.room_id
