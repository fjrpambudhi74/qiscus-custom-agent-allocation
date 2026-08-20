"""Thin wrapper around the Qiscus Multichannel (Omnichannel) API.

Endpoint paths + shapes confirmed against the real Postman collection and,
where noted, live-tested against a real account (see .claude/PLAN.md for the
full verification trail):

  - Available Agents (v2) GET /api/v2/admin/service/available_agents?room_id=
      The decision source: eligible (online, channel-matched) agents for a
      specific room, each with `current_customer_count` - the actual count
      Qiscus tracks, compared against our own configurable max-capacity to
      decide who's still free. Always called with a real room_id (from an
      incoming webhook payload or a queued entry), so its required/optional
      status in the docs doesn't matter for how we use it. Chosen over
      `Get All Agents` (v1 broken/403 for this account; v2 needs a whole
      separate admin-login flow) precisely because Get All Agents doesn't
      expose `current_customer_count` at all - without it we'd have to
      trust our own locally-tracked assignment count, which can drift from
      reality if anything changes agent load outside our webhook flow (a
      manual dashboard assignment, a race condition, downtime).
  - Assign Agent   POST /api/v1/admin/service/assign_agent
      The actual "put this agent on this room" action - this is what
      allocation.py calls once *our* logic has picked an agent.
  - Allocate Agent POST /api/v1/admin/service/allocate_agent
      Picks Qiscus's own least-busy agent, no threshold/cap param and no
      count in the response - can't enforce our configurable hard cap with
      it. Not used in the allocation flow - implemented for completeness /
      possible future use.
  - Mark As Resolved POST /api/v1/admin/service/mark_as_resolved
      Lets our side resolve a room programmatically. Not part of the core
      allocation flow (a human agent resolving via the dashboard is what's
      expected to fire the "Mark As Resolved" *webhook* to us in
      production) - its value here is as a manual testing tool: call it to
      trigger that webhook on demand instead of needing to click resolve
      in the dashboard for every test run.

All of the above use the same auth: Qiscus-App-Id + Qiscus-Secret-Key
headers (REST Token) - no admin-login Authorization token needed. POST
bodies are application/x-www-form-urlencoded, not JSON.
"""

import logging
from typing import Any, Optional

import requests

from app.config import settings

logger = logging.getLogger(__name__)

AVAILABLE_AGENTS_PATH = "/api/v2/admin/service/available_agents"
ALLOCATE_AGENT_PATH = "/api/v1/admin/service/allocate_agent"
ASSIGN_AGENT_PATH = "/api/v1/admin/service/assign_agent"
MARK_AS_RESOLVED_PATH = "/api/v1/admin/service/mark_as_resolved"


class QiscusClient:
    def __init__(self) -> None:
        self.base_url = settings.qiscus_base_url.rstrip("/")
        self.auth_headers = {
            "Qiscus-App-Id": settings.qiscus_app_id,
            "Qiscus-Secret-Key": settings.qiscus_secret_key,
        }

    def available_agents(self, room_id: str) -> list[dict[str, Any]]:
        url = f"{self.base_url}{AVAILABLE_AGENTS_PATH}"
        try:
            resp = requests.get(
                url,
                headers=self.auth_headers,
                params={"room_id": room_id},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", {}).get("agents", data if isinstance(data, list) else [])
        except requests.RequestException:
            logger.exception("Failed to fetch available agents for room %s", room_id)
            return []

    def allocate_agent(
        self,
        source: str,
        ignore_availability: Optional[bool] = None,
        channel_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Ask Qiscus for its own least-busy agent (not used by our custom
        allocation flow - kept for reference/fallback use)."""
        url = f"{self.base_url}{ALLOCATE_AGENT_PATH}"
        data = {"source": source}
        if ignore_availability is not None:
            data["ignore_availability"] = str(ignore_availability).lower()
        if channel_id is not None:
            data["channel_id"] = channel_id
        try:
            resp = requests.post(url, headers=self.auth_headers, data=data, timeout=10)
            resp.raise_for_status()
            return resp.json().get("data")
        except requests.RequestException:
            logger.exception("Failed to allocate agent (source=%s)", source)
            return None

    def assign_agent(
        self,
        room_id: str,
        agent_id: str,
        max_agent: Optional[int] = 1,
        replace_latest_agent: Optional[bool] = None,
    ) -> bool:
        url = f"{self.base_url}{ASSIGN_AGENT_PATH}"
        data = {"room_id": room_id, "agent_id": agent_id}
        if max_agent is not None:
            data["max_agent"] = max_agent
        if replace_latest_agent is not None:
            data["replace_latest_agent"] = str(replace_latest_agent).lower()
        try:
            resp = requests.post(url, headers=self.auth_headers, data=data, timeout=10)
            resp.raise_for_status()
            return True
        except requests.RequestException:
            body = resp.text if "resp" in locals() else "<no response>"
            logger.error(
                "Failed to assign agent %s to room %s - response body: %s",
                agent_id, room_id, body, exc_info=True,
            )
            return False

    def mark_as_resolved(
        self,
        room_id: str,
        notes: Optional[str] = None,
        is_send_email: Optional[bool] = None,
        extras: Optional[str] = None,
    ) -> bool:
        """Manual testing helper - resolve a room from our side to trigger
        the real 'Mark As Resolved' webhook, instead of clicking resolve in
        the dashboard for every test run."""
        url = f"{self.base_url}{MARK_AS_RESOLVED_PATH}"
        data = {"room_id": room_id}
        if notes is not None:
            data["notes"] = notes
        if is_send_email is not None:
            data["is_send_email"] = str(is_send_email).lower()
        if extras is not None:
            data["extras"] = extras
        try:
            resp = requests.post(url, headers=self.auth_headers, data=data, timeout=10)
            resp.raise_for_status()
            return True
        except requests.RequestException:
            logger.exception("Failed to mark room %s as resolved", room_id)
            return False


qiscus_client: Optional[QiscusClient] = None


def get_qiscus_client() -> QiscusClient:
    global qiscus_client
    if qiscus_client is None:
        qiscus_client = QiscusClient()
    return qiscus_client
