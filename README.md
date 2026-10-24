![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

# WorkshopPilot (EventPilot)

**A multi-agent workshop coordination system operated through ASI:One that refuses to exceed the organizer's budget or act without explicit approval.**

WorkshopPilot turns a workshop brief into an approved, budget-checked plan: it gets quotes from a supplier agent, checks capacity and cost in deterministic code, asks the organizer to approve one exact package, reserves it, settles a clearly-labelled simulated deposit, drafts an invitation that is only sent after a second explicit approval, tracks RSVPs, and re-plans when attendance changes — all inside the original budget.

It is not a generic event-planning chatbot. It is a **safe-execution layer for agents that touch money and messages**: version-bound approvals, deterministic budget authority, idempotent operations, evidence-based status, and an audit trail separating completed, pending, failed and simulated actions.

---

## 1. Problem and target users

College clubs and small teams organise workshops by hand: quotes arrive in DMs, budgets live in someone's head, approvals happen verbally, and attendance changes silently break the plan. Nobody can later answer "what was approved, by whom, and what did it cost?".

WorkshopPilot's target user is that organizer. The demo persona: a college club lead who says *"Organise a robotics workshop for 60 people in Jaipur on November 15, 2026. Keep the supplier budget under ₹20,000."* — and needs the agent to **ask before spending money or sending invitations**, every time.

## 2. Features and architecture

### The golden path (fully scripted in `tests/e2e/test_acceptance.py`)

1. Organizer sends a brief; missing details trigger an Interactive **form card**.
2. Coordinator requests quotes from the **supplier agent** (agent-to-agent trace line shown in chat: `Supplier agent -> 3 package quote(s) ... in N ms`).
3. A **package comparison carousel card** shows each package with capacity/budget badges: Starter Lab ₹14,500 (fits), Standard Lab ₹19,200 (fits), Premium Lab ₹26,000 (blocked — over budget).
4. The organizer approves ONE exact package with a short code (`approve A-7F3K`), bound to plan version, quote, price, deposit and expiry.
5. The supplier reserves (demo inventory) and returns payment terms. The coordinator's demo wallet pays a symbolic **testnet deposit**; the supplier **independently verifies the evidence** before anything is marked paid.
6. The **outreach agent** drafts an invitation (recipient addresses never leave its database). Sending requires a second approval bound to the exact content hash and recipient-set hash.
7. Attendees register through a signed RSVP link or the chat-code fallback (`RSVP AB12CD yes`). Duplicate submissions never inflate the count.
8. "Attendance is now 75" → plan v2, pending approvals invalidated, capacity conflict explained (Starter Lab no longer fits), amendment quotes issued for the deposit delta, and **nothing is auto-reserved, paid or sent**.

### Components

```
                     ASI:One (Agent Chat Protocol + Interactive Cards)
                                        |
                                        v
   +---------------------------+  quotes/reserve/pay   +---------------------------+
   |  Coordinator (buyer)      | <-------------------> |  Supplier (seller)        |
   |  event plan, approvals,   |      typed, versioned |  demo inventory, quotes   |
   |  budget authority, audit  |      message contracts|  w/ TTL, reservations,    |
   +------------+--------------+                       |  payment verification    |
                | invitations / RSVP                   +---------------------------+
                v
   +---------------------------+
   |  Outreach agent           |   allowlist-only email, signed RSVP tokens,
   |  drafts, sends, RSVP      |   chat-code fallback, count reporting
   +---------------------------+
                |  (shares outreach DB, WAL)
                v
   RSVP web service (FastAPI): GET/POST /rsvp/{token}, signed count endpoint
```

| Agent | Responsibility (and what it will NOT do) |
|---|---|
| **Coordinator** (buyer) | ASI:One conversation, per-sender event state, plan versions, approval workflow, deterministic budget/capacity checks, orchestration, audit. Never trusts client/card/LLM prices; never spends without an approval record. |
| **Supplier** (seller) | Demo inventory, expiring quotes, reservation state machine, payment terms, **independent payment verification** with transaction-id replay protection. Never trusts the coordinator's claim that a payment succeeded. |
| **Outreach** | Invitation drafts, allowlist-only sending with idempotency, signed RSVP tokens, RSVP records and counts. Never exposes attendee emails in chat, logs or URLs. |

### Fetch.ai technology used

- **uAgents** (`uagents==0.25.5`, `uagents-core==0.4.9`, pinned per the official FET payment example): three separately-running agents with distinct identities and seeds, typed message contracts, `Protocol` handlers.
- **Agent Chat Protocol (ACP)** via `uagents_core.contrib.protocols.chat` on the coordinator, so it is directly usable in **ASI:One**.
- **Interactive Cards** via the official `uagents_core.contrib.protocols.chat.cards` helpers (`CarouselCardPayload`, `FormCardPayload`, `ReviewCardPayload`, `create_card_content`, `extract_card_response`), riding in `MetadataContent` blocks. Every card is accompanied by plain-text narration so the workflow survives if a card fails to render.
- **Agentverse**: mailbox-mode registration for ASI:One reachability (see `integrations/agentverse_setup.md`).
- **ASI1 LLM** (optional, `api.asi1.ai`): intent extraction, explanations and invitation drafting — always schema-validated, numeric-guarded, and never authorized to approve/pay/send/reserve. If ASI1 is not configured the system runs deterministic templates and says so.
- **Agent Payment Protocol position**: the buyer/seller payment stages are mapped to typed contracts (see `payments/README.md`); verification is evidence-based with replay protection. This build runs the **simulated** ledger; enabling live testnet settlement requires the documented checklist.

## 3. Real vs simulated (honest status of this build)

| Capability | Status in this build | Label shown to users |
|---|---|---|
| Supplier inventory / packages | **DEMO DATA** (fictional packages in `shared/inventory.py`) | "demo inventory — not a real venue" |
| Reservation | Tracked state machine against demo inventory | "confirmed against demo inventory" |
| Payment (deposit) | **SIMULATED — NO ON-CHAIN PAYMENT** (file-backed ledger both sides verify independently) | `verified_simulated`, tx ids prefixed `SIMULATED-` |
| Payment mode switching | `PAYMENT_MODE=testnet` requires RPC config; unconfigured testnet mode is blocked with an honest message | — |
| Email | `EMAIL_PROVIDER=console` writes masked records to logs and reports **"NOT delivered"**; `smtp` provider implements real delivery (allowlist-only) | "console (development): NOT delivered" |
| RSVP | Fully working: signed tokens, dedupe, signed count endpoint, chat-code fallback | — |
| ASI1 LLM | Optional; deterministic fallback when unconfigured (this repo was built and tested without a live ASI1 key) | — |

## 4. Repository layout

```
workshop-pilot/
├── agents/            coordinator/ supplier/ outreach/   (agent.py, handlers.py, service.py each)
├── shared/            models, messages (typed contracts), enums + state machines, cards,
|                      validation (money, numeric guard), config, inventory, logging, errors
├── storage/           SQLite schemas + coordinator/supplier/outreach repositories
├── services/          planning, quote_service, approval_service, replanning_service,
|                      checklist_service, audit_service
├── integrations/      asi1_client.py, email_client.py, agentverse_setup.md
├── payments/          ledger.py (simulated chain), verification.py, protocol.py, README.md
├── rsvp_web/          FastAPI app: tokens, routes, schemas
├── scripts/           seed_demo_data, run_local, health_check, reset_demo, print_addresses
├── tests/             unit/ integration/ protocol/ e2e/
├── data/              runtime databases (gitignored; .gitkeep committed)
├── .env.example       every configuration variable, documented
└── README.md
```

## 5. Installation and configuration

Requirements: **Python 3.11+** (tested on 3.12), pip.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

1. **Seeds (required)**: give `COORDINATOR_SEED`, `SUPPLIER_SEED`, `OUTREACH_SEED` each a unique random value:
   `python3 -c "import secrets; print(secrets.token_hex(24))"`
2. **Peer addresses**: `python3 -m scripts.print_addresses` prints each agent's `agent1q...` address from the seeds; copy the supplier and outreach addresses into `SUPPLIER_ADDRESS` / `OUTREACH_ADDRESS`.
3. **RSVP secret** (required for invitations): set `RSVP_SIGNING_SECRET` to another random value.
4. Optional: `ASI1_API_KEY` (LLM features), `EMAIL_PROVIDER=smtp` + `ALLOWED_TEST_RECIPIENTS` (real email), `AGENT_MAILBOX=true` (ASI:One reachability).

Fail-fast rule: `APP_ENV=production` refuses to start when a mandatory setting is missing; `scripts/health_check.py` reports everything else.

## 6. Startup and test instructions

```bash
# seed the supplier's demo inventory (idempotent)
python -m scripts.seed_demo_data

# start everything: supplier (:8102), outreach (:8103), coordinator (:8101), RSVP (:8104)
python -m scripts.run_local

# verify health / connectivity
python -m scripts.health_check

# run the test suite
python -m pytest                     # whole suite
python -m pytest tests/unit          # money/approval core
RUN_AGENT_TESTS=1 python -m pytest tests/protocol/test_local_agents.py -m agent_network

# reset demo data (never config/secrets)
python -m scripts.reset_demo
```

Keep `run_local.py` alive for the whole demo window (mailbox mode buffers messages while the process is offline; it does not run it).

## 7. Example prompts (ASI:One)

- *"Organise a robotics workshop for 60 people in Jaipur on November 15, 2026. Keep the supplier budget under ₹20,000. Ask me before committing to a package, sending invitations, or spending funds."*
- *"Plan a college event for 75 students in Pune with a budget of ₹20,000."*
- *"Attendance is now 75"* (replan)
- *"How many have confirmed?"* (RSVP count)
- *"status" / "checklist" / "timeline" / "help" / "reset my demo"*
- Guest RSVP: *"RSVP AB12CD yes"* (code comes from the invitation email)

## 8. Demonstration walkthrough (4 minutes)

1. Cold start: "what can you do?" → capability summary + demo-data disclaimer + example prompts.
2. Deliberately incomplete brief: *"Organise a robotics workshop in Jaipur"* → form card appears → submit the full 60-person, ₹20,000 details through it.
3. Options: package comparison card with badges; Premium Lab explained as over budget; agent-to-agent trace line shown.
4. Approval: `approve A-XXXXX` for Standard Lab — review card shows the exact bound snapshot.
5. Payment: reservation + deposit settled in **simulated** mode, labelled `SIMULATED — NO ON-CHAIN PAYMENT` (state once: INR is the budgeted expense; tFET is a testnet reservation deposit — no exchange rate).
6. Invitation: draft approval → console-mode honest "NOT delivered" (or real SMTP inbox if configured).
7. RSVP: register via the chat code; count updates; duplicate attempt refused.
8. Replan: "attendance is now 75" → Starter blocked by capacity, Standard fits, amendment delta explained and approved; committed expense becomes ₹19,200 — never ₹33,700.
9. Safety: stale approval rejected; second package while reservation active rejected; audit timeline shown; limitations stated.

## 9. Agentverse and ASI:One setup

Step-by-step registration (mailbox mode, profile/README publication, Testing tab, cold-start checks) is in **`integrations/agentverse_setup.md`**. Registration does not host your process — keep `run_local.py` running on an always-on host for judging.

**Agent addresses and profile URLs** (fill in after registration):

| Agent | Address | Agentverse profile |
|---|---|---|
| Coordinator | `agent1q...` (from `scripts/print_addresses.py`) | _to be registered_ |
| Supplier | `agent1q...` | _to be registered_ |
| Outreach | `agent1q...` | _to be registered_ |

## 10. Design notes: why this is safe

- **Deterministic code owns money and authority.** Prices/eligibility come from stored quote records checked against the immutable demo catalog; the LLM and cards cannot set prices. Budget rule: `committed + proposed <= budget` with the `==` boundary deliberately allowed and tested.
- **Approvals are persisted, single-use authorization records** bound to a canonical-hash snapshot (package, quote, plan version, amount, deposit, recipient wallet / invitation content hash + recipient-set hash) with short human-readable codes. Stale plan versions, altered amounts, changed recipients, expired and replayed approvals are all rejected.
- **LLM proposes; code disposes.** ASI1 output is schema-validated; explanations pass a numeric guard (any number not present in computed facts causes a template fallback); drafting failures fall back to templates and are audit-logged.
- **Idempotency everywhere.** `action_id` keys on reservations/sends are unique-constrained in SQLite; duplicate requests return the recorded result instead of double-spending or re-sending.
- **Honest uncertainty.** Agent timeouts become visible `failed` notices; unverifiable delivery becomes `unknown` and is never blindly retried; simulated results are structurally distinct (`SIMULATED-` ids, `verified_simulated` status) and can never be presented as on-chain settlement.
- **Per-sender isolation** on every coordinator query; prompt-injection-safe handling (user/supplier text is data, never instructions); rate limiting per sender; email allowlist with no user-supplied recipients; no emails, tokens, seeds or keys in logs, URLs, or the audit trail.

## 11. Known limitations

- Supplier inventory, prices and reservations are **demo data**; no real venue booking occurs.
- The deposit is a **simulated testnet transfer** in this build; live settlement requires the checklist in `payments/README.md` and was not verified live.
- Console email is not delivery; SMTP mode requires the operator to configure a provider and allowlist.
- An RSVP registration is not proof of attendance; seeded counts are demo data.
- The proposed per-event coordination fee is a business hypothesis, not validated demand.
- No refund flow; downgrades after payment are out of scope ("no refund flow in this demo").
- ASI:One card rendering and mailbox delivery must be verified in the live environment (the code uses the official chat/cards SDK contracts).

## 12. Submission links (to be filled at submission)

- ASI:One shared chat: _pending demo recording_
- Demo video (3–5 min): _pending_
- Devpost submission: _pending_

## 13. Versions and compatibility

Pinned per the official Fetch.ai FET payment example and verified in this environment: `uagents==0.25.5`, `uagents-core==0.4.9` (message models are pydantic-v1-based via `uagents_core.models`), Python 3.12. The Interactive Cards implementation uses the official `uagents_core.contrib.protocols.chat.cards` module shipped in `uagents-core==0.4.9` (card kinds: `carousel | detail | form | review | custom`; cards travel in `MetadataContent`; metadata values are strings; payload ceiling enforced at 64 KB).

## 14. License

MIT — see `LICENSE`.
