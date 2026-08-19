# Plan: Qiscus Custom Agent Allocation Test

Source spec: `Qiscus_Custom_Agent_Allocation_Test.pdf` (Downloads).
Current status: scaffold + core logic done and unit-tested locally. Everything
below is what's left before this can be submitted.

## Phase 0 — Done

- [x] FastAPI + SQLite (SQLModel) + APScheduler scaffold
- [x] Allocation logic: max-capacity per agent, online-only filter, FIFO queue
- [x] Resolved-webhook handler that drains the queue
- [x] ERD + README
- [x] Local smoke test (mocked Qiscus API) confirms capacity + FIFO behave correctly

## Phase 1 — API verification (in progress)

- [x] Confirmed real Custom Agent Allocation webhook payload shape from
      official docs (flat fields; `candidate_agent` is a single object, not
      a list) - `app/schemas.py` and `app/allocation.py` updated + re-tested
- [x] Confirmed `Get All Agents`: `GET /api/v1/admin/agents?page=&limit=`
      (also exists as `GET /api/v2/admin/agents?cursor_before=&cursor_after=&limit=&search=&scope`,
      but v2 needs `Authorization: {{AdminToken}}` + Qiscus-App-Id only, no
      Secret-Key, i.e. a login flow - stick with v1's simpler
      App-Id/Secret-Key auth) - wired into `app/qiscus_client.py`.
      v2's confirmed response shape (`data.agents[]` with `id`, `name`,
      `email`, `is_available`, `force_offline`, `avatar_url`,
      `user_channels`, `user_roles`) is a useful field-name reference even
      though we call v1 - v1's exact response hasn't been seen directly,
      but the Agent object shape should match.
- [x] Found `Allocate Agent` = the "least active agent" API referenced in
      docs: `POST /api/v1/admin/service/allocate_agent`, form-urlencoded
      body (`source`, `ignore_availability`, `channel_...` - not fully seen
      yet), auth via Qiscus-App-Id/Secret-Key only. Returns an agent object,
      not a room assignment.
- [x] Confirmed `Assign Agent`: `POST /api/v1/admin/service/assign_agent`,
      form-urlencoded body (`room_id`, `agent_id` required; `max_agent`,
      `replace_latest_agent` optional), same Qiscus-App-Id/Secret-Key auth,
      response `data.added_agent`. This is the action `allocation.py` calls
      once our own logic picks an agent - wired into `qiscus_client.py`
      (`assign_agent()`, defaults `max_agent=1` as a race-condition guard)
      and re-tested (mocked call captures the exact room_id/agent_id/max_agent
      sent).
  - There's also `Allocate and Assign Agent`
    (`/admin/service/allocate_assign_agent`) which does Qiscus's own
    least-busy pick + assignment in one call - docs even say "use this if
    you have custom agent allocation enabled" - but we deliberately don't
    use it, since it applies Qiscus's own busyness ranking instead of our
    configurable max-capacity/FIFO rule. Noted here in case that decision
    needs revisiting.
- [x] Confirmed `Mark As Resolved` webhook payload shape from official docs
      - it's nested (`service.room_id`, `service.is_resolved`, etc, plus
      `channel`/`customer`/`resolved_by` objects), NOT flat like the earlier
      guess had it. Fixed `ResolvedWebhookPayload` in `app/schemas.py`
      (`room_id` is now a `@property` reading `service.room_id`) and
      re-tested end-to-end (allocate then resolve, assignment correctly
      flips to `resolved`).
  - CORRECTION (2026-08-18): earlier entry here claimed registering this
    webhook needs a separate `Authorization: AdminToken` API call
    (`/app/webhook/mark_as_resolved`) - that was a WebFetch
    mis-synthesis and is likely wrong; drop it. What's actually confirmed
    from the real Postman collection is a *different*, simpler endpoint:
    `POST /api/v1/admin/service/mark_as_resolved` (Qiscus-App-Id/Secret-Key
    auth, form-urlencoded `room_id` required + `notes`/`is_send_email`/
    `extras` optional) - this resolves a room programmatically. It's the
    API the test PDF's "gunakan api ini untuk setting webhook mark as
    resolve" note is pointing at: use it as a manual trigger to fire the
    real Mark As Resolved *webhook* to our server during testing, instead
    of needing to click resolve in the dashboard each time. Wired into
    `qiscus_client.py` as `mark_as_resolved()`. The webhook URL itself is
    still expected to be registered the same simple way as the other
    webhook types - via the Settings page toggle+URL field, no special
    token needed (per documentation.qiscus.com/omnichannel-chat/webhook:
    "Each webhook can be registered and configured with its own URL
    endpoint and enable/disable toggle").
- [x] Got real App ID/Secret Key, filled into `.env`. Live-tested
      `allocate_agent` (200 OK) - confirmed credentials are valid.
- [x] **`Get All Agents` v1 root-caused**: live-tested with App-Id/Secret-Key
      (403), then with a real AdminToken added on top (still 403, identical
      `traceid` both times) - conclusively an infra-side block on Qiscus's
      end for this account/endpoint specific to v1, not a credentials or
      code issue. Confirmed the fix: v2 (`/api/v2/admin/agents`) with
      `Authorization: <AdminToken>` + `Qiscus-App-Id` returns 200 OK
      (live-tested, currently 0 agents registered on this app - expected,
      account is new).
  - Switched `qiscus_client.py` to v2 permanently: added `_login()` (POST
    `/api/v1/auth` with `QISCUS_ADMIN_EMAIL`/`QISCUS_ADMIN_PASSWORD`,
    caches the token) and `_admin_headers()` (auto re-login once on a 403
    in case the cached token expired). `get_all_agents()` now calls v2.
  - Added `qiscus_admin_email`/`qiscus_admin_password` to `app/config.py`
    and `.env` (real admin login, gitignored - not committed).
- [ ] Once agents actually exist on this test app: call `assign_agent`
      against the live API with a real room_id/agent_id (still only
      mock-tested so far), and register both webhooks (Custom Agent
      Allocation via Settings toggle, Mark As Resolved the same way)

## Phase 1.5 — Architecture pivot: `Get All Agents` → `Available Agents (v2)` (SUPERSEDES parts of Phase 1)

Prompted by a colleague's review (2026-08-19). Reasoning, fully settled:

- `Get All Agents` (v1 or v2) doesn't expose a `current_customer_count`
  field at all - only `is_available`. The spec's core requirement is a
  **hard, configurable cap** per agent, which needs the actual count, not
  just an online/offline flag.
- `Available Agents (v2)` (`GET /admin/service/available_agents?room_id=`)
  *does* have `current_customer_count`, and uses the same simple
  Qiscus-App-Id/Secret-Key auth as everything else - no admin-login flow
  needed at all now.
- `room_id` is required by this endpoint (Postman docs don't label it
  required/optional explicitly, but this is moot: we always have a real
  `room_id` on hand wherever we'd call it - from the allocation webhook
  payload, or from a `QueueEntry` - so there was never a scenario where
  we'd need to call it without one).
- This also closes a correctness gap in the old design: local
  `Assignment`-count-based capacity tracking could drift from reality if
  anything changed an agent's load outside our webhook flow (manual
  dashboard assignment, race condition, downtime). Reading
  `current_customer_count` live from Qiscus avoids that entirely.

**What changed as a result:**
- `app/qiscus_client.py`: removed `_login()`/`_admin_headers()`/
  `get_all_agents()`/`AUTH_PATH`/`AGENTS_PATH_V2` entirely. Added
  `available_agents(room_id)`.
- `app/allocation.py`: removed local capacity tracking
  (`_active_count()`), the local agent cache (`_upsert_agent_from_candidate()`,
  `refresh_agents_from_api()`). New `get_available_agent_for_room(room_id)`
  calls `available_agents()` live and compares `current_customer_count`
  against `settings.default_max_capacity`. `process_queue()` now checks
  the local queue table *before* any API call (queue empty → zero API
  calls per tick) and looks up each queued entry's own `room_id` rather
  than a room-agnostic "is anyone free" check - also means it stops at the
  first un-servable entry instead of skipping ahead, preserving strict FIFO.
- `app/models.py`: dropped the `Agent` table entirely - no online
  status/capacity is cached locally anymore. `Assignment.agent_id` (int FK)
  became `Assignment.qiscus_agent_id` (str, no FK) - kept purely as an
  audit/history record, not used for capacity math.
- `app/scheduler.py`: dropped the `Get All Agents` refresh call - the poll
  job now *only* retries `process_queue()` (still needed: Qiscus has no
  webhook for "agent came back online", so a queued customer needs
  *something* periodically re-checking on their behalf).
- `app/config.py`, `.env`, `.env.example`: removed
  `qiscus_admin_email`/`qiscus_admin_password` - no longer used anywhere.
- `README.md`: ERD, "How it works", "Configuration", and "API notes"
  sections updated to match.
- Re-tested end-to-end (mocked `available_agents`/`assign_agent`): 2 agents
  × cap 2 → 4 direct assigns + 1 queued, resolve drains the queued one, and
  an empty-queue scheduler tick makes zero API calls - confirmed.
- `Allocate Agent` and `Mark As Resolved` unaffected - still implemented,
  still intentionally unused by the main flow (see `qiscus_client.py`
  docstring for why).

## Phase 2 — Deployment

- [ ] Choose host (Railway or Render — both have free tier + persistent
      process, fine for SQLite)
- [ ] Add deploy config (Procfile / railway.json, whichever host is picked)
- [ ] Set env vars on host (QISCUS_APP_ID, QISCUS_SECRET_KEY, etc.)
- [ ] Deploy, confirm `/health` responds on the public HTTPS URL

## Phase 3 — Wire up in Qiscus dashboard

- [x] Settings → **Custom Agent Allocation** toggle enabled, webhook URL set
      to `https://web-production-61b1b.up.railway.app/webhook/allocation`
      via dashboard UI (confirmed via API readback:
      `allocate_agent_webhook_url` + `is_allocate_agent_webhook_enabled: true`)
- [x] **Mark As Resolved** webhook registered via
      `POST https://omnichannel.qiscus.com/api/v1/app/webhook/mark_as_resolved`
      (AdminToken auth) - no dashboard UI page exists for this one, had to
      use the API directly. Confirmed via API readback:
      `mark_as_resolved_webhook_url` + `is_mark_as_resolved_webhook_enabled: true`
- [x] 2 test agents added + logged in (needed to actually log in as the
      agent for `is_available` to flip true - just creating the agent
      record in Agents Management wasn't enough)

## Phase 4 — End-to-end verification (DONE, live on Railway, 2026-08-19)

Full real-traffic log trail (Qiscus Live Chat widget as the test channel,
`sjxif-8cyxqwtoufmcimb`, DEFAULT_MAX_CAPACITY=2, 2 real agents):

1. Chat 1 (`room=468980783`) arrives before any agent is online → queued
2. Agent A logs in/online → next scheduler tick (`poll_and_drain_queue`)
   auto-assigns chat 1 to agent 192563, with zero new webhook call needed
3. Chat 2 arrives → agent 192563 has 1 active room (<2 cap) → assigned
   directly via the webhook path (no queueing needed)
4. Chat 3 arrives → agent 192563 now has 2 active rooms (at cap) → queued
   - **confirms the hard cap of 2 is actually enforced**, not just
     "assign to least busy"
5. Agent B (192564) logs in/online → scheduler auto-assigns chat 3 to them
6. Chat 4 arrives → agent 192564 has 1 active room → assigned directly
7. Chat 5 arrives → **both** agents now at 2/2 → queued
   - confirms the cap holds independently per-agent, across multiple agents
8. Chat 1 gets resolved (Mark As Resolved webhook fires) → agent 192563's
   slot frees → chat 5 (oldest still-queued, correct FIFO order) gets
   assigned **immediately in the same webhook request** (not waiting for
   the next scheduler tick) - confirms `handle_resolved()`'s inline
   `process_queue()` call works as designed, live

Every core requirement from the test PDF is now verified against real
Qiscus traffic, not just mocks: max-configurable-capacity per agent, FIFO
queueing, online-only assignment, and auto-recovery for the "no webhook for
agent coming back online" gap.

Not yet explicitly tested (lower priority, logic is shared with what's
already proven above so risk is low): explicit restart-survival check for
SQLite/volume persistence across a redeploy.

## Phase 5 — Submission

- [ ] Push repo to GitHub (public or invite the reviewer)
- [ ] Write submission email: App ID used, live service URL, GitHub link
- [ ] Keep the deployed service running until testing is confirmed complete

## Open risks / things to double check

- All endpoint paths + payload schemas now confirmed against the real
  Postman collection and/or live-tested (see Phase 1 / Phase 1.5) - no
  remaining guesses.
- No Qiscus webhook exists for "agent came back online" — relying on the
  APScheduler poll (`POLL_INTERVAL_SECONDS`) as the catch-up mechanism for
  the FIFO queue specifically (not for a local status cache anymore - see
  Phase 1.5); confirm the poll interval is short enough for the test
  reviewer's patience (default 30s).
- `available_agents(room_id)` is called once per queued entry per poll
  tick and once per incoming webhook - fine at this test's scale, but
  would need batching/rate-limit awareness at higher volume.
- SQLite is fine for this test's scope; if the host's filesystem isn't
  persistent (some serverless platforms), the DB resets on redeploy — pick
  a host with a persistent disk/volume, or swap to managed Postgres.
