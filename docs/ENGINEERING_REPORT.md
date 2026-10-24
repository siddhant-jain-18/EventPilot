# WorkshopPilot — Engineering Report (coding agent handback)

Date: 2026-10-24. Scope: full implementation of the Implementation Handoff
("EventPilot" build specification) reconciled with Plan.md, delivered as a
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
- **Operational scripts**: `seed_demo_data`, `run_local` (3 agents + RSVP via
  uvicorn), `health_check`, `reset_demo`, `print_addresses`.
- **Tests**: 156 passed, 1 skipped (optional live-agent smoke test;
  `RUN_AGENT_TESTS=1` runs it — it passed in this environment).

## 2. Verification (exact commands and results)

```
python -m pytest                         -> 156 passed, 1 skipped in ~3.3s
RUN_AGENT_TESTS=1 python -m pytest tests/protocol/test_local_agents.py -m agent_network
                                         -> 1 passed (deterministic distinct agent1q... identities)
python -m scripts.seed_demo_data         -> seeded 3 demo packages (verified output)
python -m scripts.reset_demo             -> cleared demo state (verified)
python -m scripts.print_addresses        -> prints agent addresses from seeds (verified shape)
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
pip install -r requirements.txt
cp .env.example .env          # fill seeds, addresses, RSVP secret
python -m scripts.seed_demo_data
python -m scripts.run_local   # supplier :8102, outreach :8103, coordinator :8101, RSVP :8104
python -m scripts.health_check
```

## 4. Environment variables

All documented in `.env.example`. Required for local run: `COORDINATOR_SEED`,
`SUPPLIER_SEED`, `OUTREACH_SEED`, `SUPPLIER_ADDRESS`, `OUTREACH_ADDRESS`,
`RSVP_SIGNING_SECRET`, `ALLOWED_TEST_RECIPIENTS`. Optional: `ASI1_API_KEY`
(LLM features; deterministic fallbacks run without it), `EMAIL_PROVIDER=smtp`
+ SMTP settings (real delivery), `PAYMENT_MODE=testnet` + `PAYMENT_RPC_URL`
(live settlement checklist in `payments/README.md`).

## 5. Deployment / payment / email status

- Deployment to Agentverse/ASI:One and public hosting: **not performed here** —
  manual steps in `integrations/agentverse_setup.md`; agent addresses in the
  README must be filled after first run/registration.
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

1. Fill `.env` (seeds, peer addresses, RSVP secret) via
   `scripts/print_addresses.py`.
2. Register the three agents on Agentverse (mailbox mode) and paste the
   profile URLs + addresses into README section 9.
3. Test in a fresh ASI:One session; record the shared chat and the 3–5 min
   demo video; submit via Devpost.
4. Optional: configure ASI1 key, SMTP provider + allowlist, and — only after
   the checklist in `payments/README.md` — testnet payment mode.
