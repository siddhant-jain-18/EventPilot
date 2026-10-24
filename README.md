![Fetch-A-Thon 3.0](https://img.shields.io/badge/Fetch--A--Thon%203.0-innovationlab-3D8BD3)
![Hackathon project](https://img.shields.io/badge/hackathon-Fetch.ai-5F43F1)
![Python](https://img.shields.io/badge/python-3.11%2B-3776AB)

# WorkshopPilot (EventPilot)

WorkshopPilot is a Fetch.ai multi-agent event coordination system for the
Fetch-A-Thon 3.0 hackathon. It helps an organiser plan a workshop, compare
supplier packages, request approval before committing money, send an approved
invitation, collect RSVPs, and re-plan when attendance changes.

The key idea is **safe execution**: agents can propose and explain, but
deterministic application code owns budget checks, approvals, payment
verification, recipient allowlists, idempotency, and the audit trail.

> **Demo status:** this repository uses fictional supplier inventory, a
> simulated payment ledger, and console email by default. It does not book a
> real venue, make an on-chain payment, or deliver email unless explicitly
> configured.

## What to show a judge

Start with this prompt in ASI:One:

> Organise a robotics workshop for 60 people in Jaipur on November 15, 2026.
> Keep the supplier budget under ₹20,000. Ask me before committing to a
> package, sending invitations, or spending funds.

Then demonstrate:

1. Missing details trigger an interactive form.
2. The supplier returns Starter ₹14,500, Standard ₹19,200, and Premium ₹26,000
   (blocked by the budget).
3. One exact package is approved with a short code.
4. Reservation and the symbolic deposit happen only after approval. The
   payment says **SIMULATED — NO ON-CHAIN PAYMENT**.
5. Invitation sending requires a second approval bound to the exact content
   and recipient set.
6. Signed RSVP links and chat-code RSVPs are deduplicated.
7. `Attendance is now 75` creates plan v2, invalidates stale approvals, and
   waits for approval again instead of auto-spending.

Useful commands are `status`, `checklist`, `timeline`, `help`,
`How many have confirmed?`, and `reset my demo`.

## Architecture in one minute

```text
ASI:One / Agent Chat Protocol
             |
             v
  Coordinator agent (:8101)
    | quotes / reservation / payment evidence
    +-----------------------> Supplier agent (:8102)
    |
    + invitation draft / send / RSVP count
              |
              v
       Outreach agent (:8103)
              |
              v
       RSVP API (:8104)
```

The three agents use `uagents` with separate identities and typed message
contracts. The coordinator uses the official Chat Protocol and Interactive
Cards from `uagents-core`, making it suitable for an ASI:One conversation.

| Component | Responsibility | Safety boundary |
|---|---|---|
| Coordinator | Plans, approvals, orchestration, audit | Never spends or sends without a matching approval |
| Supplier | Demo inventory, quotes, reservations, payment verification | Does not trust the coordinator's payment claim |
| Outreach | Invitation drafts, allowlist-only sends, signed RSVPs | Never exposes attendee emails in chat or URLs |
| RSVP API | Signed registration and deduplicated counts | Tokens are scoped and expiry checked |

## Quick start: local demo

Requirements: Python 3.11+ and pip. Python 3.12 is the tested runtime.

```bash
git clone <repository-url>
cd EventPilot
python3 -m venv .venv
source .venv/bin/activate                 # Windows: .venv\\Scripts\\activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Generate four values and paste them into `.env`:

```bash
python - <<'PY'
import secrets
for name in ("COORDINATOR_SEED", "SUPPLIER_SEED", "OUTREACH_SEED", "RSVP_SIGNING_SECRET"):
    print(f"{name}={secrets.token_hex(24)}")
PY
```

Each agent must have a different seed. Never commit `.env` or reuse production
keys. Derive the peer addresses:

```bash
python -m scripts.print_addresses
```

Copy the printed SUPPLIER and OUTREACH addresses into `SUPPLIER_ADDRESS` and
`OUTREACH_ADDRESS` in `.env`. Keep these safe defaults:

```dotenv
APP_ENV=development
PAYMENT_MODE=simulated
EMAIL_PROVIDER=console
AGENT_MAILBOX=false
ALLOWED_TEST_RECIPIENTS=alice@example.test,bob@example.test
```

Seed and start the stack:

```bash
python -m scripts.seed_demo_data
python -m scripts.run_local
```

| Service | URL | Purpose |
|---|---|---|
| Coordinator | `http://127.0.0.1:8101` | ASI:One/uAgents conversation |
| Supplier | `http://127.0.0.1:8102` | Quotes and reservations |
| Outreach | `http://127.0.0.1:8103` | Invitations and RSVP coordination |
| RSVP API | `http://127.0.0.1:8104` | Signed RSVP links and counts |

Leave the launcher running during the demo. In another terminal:

```bash
source .venv/bin/activate
python -m scripts.health_check
```

Stop with Ctrl+C. Reset demo state with `python -m scripts.reset_demo`.

## Tests

```bash
python -m pytest                         # complete suite
python -m pytest tests/unit               # deterministic business rules
python -m pytest tests/integration        # RSVP/service integration
RUN_AGENT_TESTS=1 python -m pytest tests/protocol/test_local_agents.py -m agent_network
```

The golden path is covered by `tests/e2e/test_acceptance.py` using an
in-memory bus, so it does not require live Agentverse, email, or blockchain.

## Fetch.ai and ASI:One

This project uses:

- `uagents==0.25.5` for separately running coordinator, supplier, and outreach
  agents;
- `uagents-core==0.4.9` for the Agent Chat Protocol and Interactive Cards;
- Agentverse mailbox mode so the coordinator can be reached from ASI:One;
- optional ASI1 access for intent extraction and invitation drafting. Without
  `ASI1_API_KEY`, deterministic templates are used and the user is told so.

See [`integrations/agentverse_setup.md`](integrations/agentverse_setup.md) for
registration, mailbox, and fresh-session testing. Agentverse registration does
not host the Python process: keep `scripts.run_local` running on a laptop, VM,
or container.

## Configuration and honest boundaries

All settings are documented in [`.env.example`](.env.example). Defaults are
intentionally safe for a hackathon demo:

- `PAYMENT_MODE=simulated`: file-backed ledger, not blockchain settlement.
- `EMAIL_PROVIDER=console`: masked records, explicitly **NOT delivered**.
- Empty `ASI1_API_KEY`: deterministic fallbacks, no LLM dependency.
- Packages in `shared/inventory.py`: fictional demo inventory.

The INR budget is the event expense. The tFET deposit is a separate symbolic
reservation deposit; no INR/tFET exchange rate exists. Read
[`payments/README.md`](payments/README.md) before enabling testnet mode.

## Troubleshooting

**`ModuleNotFoundError: uagents_core`** — activate `.venv` and install with
`python -m pip install -r requirements.txt`.

**Missing peer addresses** — fill all three seeds, then run
`python -m scripts.print_addresses` and copy the two peer addresses to `.env`.

**RSVP service is not reachable** — confirm port 8104 is free and run
`python -m scripts.health_check`. The launcher uses the Uvicorn factory
`rsvp_web.app:app_from_env`.

**ASI:One cannot reach the coordinator** — set `AGENT_MAILBOX=true`, register
the coordinator in Agentverse, and keep the local process alive. Verify the
address was generated from the same seed currently in `.env`.

**Payment says simulated** — expected. This build deliberately does not claim
an on-chain transfer.

**A test appears to hang** — run the unit suite first, then integration tests
separately. Live agent tests are opt-in with `RUN_AGENT_TESTS=1`.

## Repository map

```text
agents/       coordinator, supplier, and outreach uAgents
services/     planning, quotes, approvals, replanning, audit
shared/       models, contracts, validation, cards, configuration
payments/     simulated ledger and independent verification
storage/      SQLite schemas and repositories
rsvp_web/     FastAPI RSVP service
scripts/      seed, run, health, reset, and address utilities
tests/        unit, integration, protocol, and acceptance coverage
docs/         engineering handback and operational notes
```

## Known limitations and submission checklist

The current build does not include real venue booking, refunds, live testnet
settlement, or guaranteed SMTP delivery. Live ASI:One card rendering and
Agentverse mailbox reachability must be checked in the deployment environment.

Before submitting to Fetch-A-Thon 3.0:

1. Run and record the local golden-path demo.
2. Register all three agents in Agentverse and verify a fresh ASI:One session.
3. Add agent addresses, profile URLs, shared chat, and demo video to the
   submission materials.
4. State clearly that payments are simulated and inventory is demo data.

## License

MIT — see [`LICENSE`](LICENSE).
