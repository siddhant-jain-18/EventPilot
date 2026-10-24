# EventPilot — Engineering Report (coding agent handback)

Date: 2026-10-24. Scope: implementation and documentation handback for the
Fetch-A-Thon 3.0 build, delivered as a
GitHub-ready repository. This report states what was actually verified in this
environment and what was not.

## 1. Implemented

- **Three separately running uAgents** (`uagents==0.25.5`, `uagents-core==0.4.9`,
  verified installed and importable in this environment) with distinct
  identities: `agents/coordinator`, `agents/supplier`, `agents/outreach`.
- **ASI:One-facing coordinator** using the official Agent Chat Protocol
  (`uagents_core.contrib.protocols.chat`: `ChatMessage`, `ChatAcknowledgement`,
  `TextContent`, `StartSessionContent`, `EndSessionContent`).
- **Interactive Cards via the OFFICIAL SDK cards module**
  (`uagents_core.contrib.protocols.chat.cards`): `FormCardPayload` (brief form),
  `CarouselCardPayload` (package comparison), `ReviewCardPayload` (package and
  invitation approvals) in `MetadataContent`; 64 KB payload ceiling and
  string-only metadata enforced in `shared/cards.py`; plain-text narration
  always accompanies a card; responses parsed with `extract_card_response`.
- **Typed, versioned message contracts** in `shared/messages.py`
  (Quote/Reservation/PaymentStatus/InvitationDraft/SendInvitation/RsvpCount/
  RsvpSubmit + supersede), each with schema_version, request_id, correlation_id,
  typed status, structured error and an honest `side_effect` field.
- **Deterministic planning** (`services/planning.py`): capacity, equipment,
  availability, budget (integer paise; `==` boundary allowed) and quote-validity
  rules; 60/75-attendee scenarios covered by tests.
- **Approvals** (`services/approval_service.py`): single-use, hash-bound
  snapshots with short codes (`A-XXXXX`), plan-version binding, expiry,
  invalidation on replan; bare "yes" refused when more than one action is pending.
- **Simulated payment chain** (`payments/ledger.py`, file-backed JSONL both
  sides treat as authoritative) with **independent supplier-side verification**
  (`payments/verification.py`), per-transfer/per-event caps, balance checks,
  and transaction-id replay protection (SQLite unique table). Real testnet
  verification (`RestChainVerifier` against a Cosmos REST endpoint) is
  implemented and mock-tested; enabling it live requires the checklist in
  `payments/README.md` (not verified against a live chain in this build).
- **Invitation + RSVP flow**: template drafts (ASI1 hook included), allowlist
  enforcement, content/recipient-set hash binding, idempotent sends, console
  provider labelled "NOT delivered", SMTP provider with unknown-outcome
  handling; signed RSVP tokens (HMAC), duplicate-safe registrations, signed
  count endpoint, chat-code RSVP fallback.
- **Replanning** (`services/replanning_service.py` + coordinator flow): plan
  versioning, approval invalidation, supersede vs amendment semantics —
  including the amendment-after-payment path where committed expense becomes
  ₹19,200 (never stacked) with a 0.03 tFET deposit delta.
- **Persistence**: three SQLite databases (WAL) with explicit schemas;
  per-sender isolation enforced in every owner-scoped query; restart-safe.
- **Audit trail** (`services/audit_service.py`): append-only, correlation ids,
  sanitized metadata (emails/secrets/tokens redacted), `timeline` command.
- **Reliability**: outbound-request tracking with honest timeout handling
  (`failed` + user notice; payment `unknown` rather than blind retry),
  idempotency keys on reservations/sends/payments, per-sender rate limiting,
  cold-start greeting with demo-data disclaimer, graceful off-topic handling,
  `reset my demo` scoped per sender.
- **Operational scripts**: `init_env` (cross-OS .env bootstrap with generated
  secrets and peer addresses), `seed_demo_data`, `run_local` (3 agents + RSVP
  via uvicorn, preflight validation, crash monitoring), `health_check` (RSVP +
  all three agent endpoints), `reset_demo`, `print_addresses`, `chat_cli`
  (local Agent Chat Protocol driver). `Dockerfile` / `.dockerignore` provide a
  single-container deployment.
- **ASI:One card round-trip**: card clicks are accepted as validated
  MetadataContent responses, as JSON text blocks (direct @mention), and as
  planner-mediated prose naming the approval code; form submissions accept
  nested `fields`, `form`/`values`, or flat field keys. Unexpected turn errors
  are converted into an honest reply instead of a silent failure.
- **Console-mode RSVP demo**: when `EMAIL_PROVIDER=console` nothing is emailed
  and the coordinator lists the single-use chat RSVP codes for the allowlisted
  test recipients (never attendee addresses). With SMTP configured, codes
  travel only inside the email.
- **Mailbox correctness**: agents no longer pass a hard-coded local endpoint
  when `AGENT_MAILBOX=true` (uAgents lets an explicit endpoint override the
  relay). `AGENT_ENDPOINT` supports public deployments; configuring both is a
  startup error.
- **Tests**: unit, integration, protocol, and acceptance coverage are included.
  Run the deterministic unit suite first; live-agent tests are opt-in.

## 2. Verification (exact commands and results)

```
python -m pytest tests/unit               # deterministic business rules
python -m pytest tests/integration        # workflow, cards, RSVP integration
python -m pytest                          # complete suite
RUN_AGENT_TESTS=1 python -m pytest tests/protocol/test_local_agents.py -m agent_network
python -m scripts.init_env                # writes .env (fresh checkout)
python -m scripts.seed_demo_data          # seeds 3 fictional packages
python -m scripts.print_addresses        # prints addresses from configured seeds
python -m scripts.health_check            # RSVP + 3 agent endpoints
```

Environment: Python 3.12.15; packages installed and pinned in
`requirements.txt` (`uagents==0.25.5`, `uagents-core==0.4.9`, fastapi 0.115.6,
uvicorn 0.32.1, httpx 0.27.2, python-dotenv 1.0.1, pytest 8.3.4).

The full golden path is also exercised end-to-end by
`tests/e2e/test_acceptance.py` through an in-memory bus that mirrors the
uAgents handler layer (all sixteen acceptance steps asserted).

## 3. Run instructions

```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt
python -m scripts.init_env    # seeds, RSVP secret and peer addresses
python -m scripts.seed_demo_data
python -m scripts.run_local   # supplier :8102, outreach :8103, coordinator :8101, RSVP :8104
python -m scripts.health_check
```

Windows activation differs (`py -3.12 -m venv .venv`,
`.venv\Scripts\Activate.ps1` or `.venv\Scripts\activate.bat`); the README
documents macOS/Linux, PowerShell, and Command Prompt paths.

## 4. Environment variables

All documented in `.env.example`. Required for local run: `COORDINATOR_SEED`,
`SUPPLIER_SEED`, `OUTREACH_SEED`, `SUPPLIER_ADDRESS`, `OUTREACH_ADDRESS`,
`RSVP_SIGNING_SECRET`, `ALLOWED_TEST_RECIPIENTS`. Optional: `ASI1_API_KEY`
(LLM features; deterministic fallbacks run without it), `EMAIL_PROVIDER=smtp`
+ SMTP settings (real delivery), `PAYMENT_MODE=testnet` + `PAYMENT_RPC_URL`
(live settlement checklist in `payments/README.md`), `AGENT_MAILBOX=true`
(Agentverse relay), `AGENT_ENDPOINT` (public `/submit` URL; mutually exclusive
with mailbox), `RSVP_BASE_URL` (public RSVP URL), `RSVP_PORT` (falls back to
the platform `PORT`, then 8104).

## 5. Deployment / payment / email status

- Deployment to Agentverse/ASI:One and public hosting: **not performed here** —
  registration requires an Agentverse account. Manual steps for mailbox,
  public-endpoint, Docker and single-port PaaS deployments are in
  `integrations/agentverse_setup.md`; agent addresses in the README table must
  be filled after first run/registration. Mailbox mode was verified to start
  the mailbox client locally (Agentverse reported it needs a mailbox created
  in the Inspector, which is the documented one-time step).
- Payment: **SIMULATED — NO ON-CHAIN PAYMENT** was the mode used and verified.
  No live testnet transfer, faucet funding, or RPC verification was performed.
- Email: **console mode** (masked log records, explicitly "NOT delivered").
  SMTP delivery was mock-tested only; no live provider was configured.

## 6. Known limitations

- Demo inventory only; reservations are not real bookings; tFET is not INR and
  no exchange rate exists in code.
- ASI:One card rendering and mailbox delivery must be verified live (the code
  uses the official chat/cards contracts of the pinned SDK).
- ASI1 extraction is prompt-based JSON with strict re-validation; without an
  API key the deterministic parser alone drives the flows (by design).
- No refund flow; downgrades after payment are out of scope.

## 7. Manual actions for the project owner

1. Fill `.env` with `python -m scripts.init_env` (seeds, RSVP secret, peer
   addresses).
2. Register the coordinator on Agentverse (mailbox or public endpoint) and
   paste the profile URL + addresses into the README section "Registration
   details judges need".
3. Test in a fresh ASI:One session, including one card click; record the
   shared chat and the 3–5 min demo video; submit via Devpost.
4. Optional: configure ASI1 key, SMTP provider + allowlist, and — only after
   the checklist in `payments/README.md` — testnet payment mode.
