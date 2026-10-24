![Fetch-A-Thon 3.0](https://img.shields.io/badge/Fetch--A--Thon%203.0-innovationlab-3D8BD3)
![Hackathon project](https://img.shields.io/badge/hackathon-Fetch.ai-5F43F1)
![Python](https://img.shields.io/badge/python-3.11%2B-3776AB)

# EventPilot (EventPilot)

EventPilot is a Fetch.ai multi-agent event coordination system for the
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

**Contents:** [Demo](#run-the-judge-demo) · [Local setup](#local-setup-all-operating-systems)
· [Endpoints](#endpoint-reference) · [Agentverse / public deployment](#deploy-for-agentverse-and-asi-one)
· [Troubleshooting](#troubleshooting) · [Tests](#tests) · [Limitations](#known-limitations-and-submission-checklist)

## What to show a judge

Start with this prompt in ASI:One (or in the local chat client described
below):

> Organise a robotics workshop for 60 people in Jaipur on November 15, 2026.
> Keep the supplier budget under ₹20,000. Ask me before committing to a
> package, sending invitations, or spending funds.

Then demonstrate:

1. Missing details trigger an interactive form (or plain-text questions if
   cards are unavailable).
2. The supplier returns Starter ₹14,500, Standard ₹19,200, and Premium ₹26,000
   (blocked by the budget).
3. One exact package is approved with a short code (`approve A-7F3KX`).
4. Reservation and the symbolic deposit happen only after approval. The
   payment says **SIMULATED — NO ON-CHAIN PAYMENT**.
5. Invitation sending requires a second approval bound to the exact content
   and recipient set.
6. In console mode the chat shows one single-use RSVP code per allowlisted
   test recipient (nothing was emailed). With SMTP configured, the link and
   code travel only in the email. Signed links and chat codes are deduplicated.
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

## Local setup (all operating systems)

Requirements: Python 3.11+ (3.12 is the tested runtime), pip, and internet
access for the first dependency install. No Docker, database server, or ASI1
key is required.

### 1. Clone and create a virtual environment

**macOS / Linux**

```bash
git clone https://github.com/siddhant-jain-18/EventPilot.git
cd EventPilot
python3 -m venv .venv
source .venv/bin/activate
```

**Windows PowerShell** (`python` may be `py -3.12` if the launcher is used)

```powershell
git clone https://github.com/siddhant-jain-18/EventPilot.git
cd EventPilot
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
```

If PowerShell blocks the activation script, permit it for this terminal only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.venv\Scripts\Activate.ps1
```

**Windows Command Prompt**

```bat
git clone https://github.com/siddhant-jain-18/EventPilot.git
cd EventPilot
py -3.12 -m venv .venv
.venv\Scripts\activate.bat
```

### 2. Install dependencies (same command everywhere)

```bash
python -m pip install -r requirements.txt
```

`python -m pip` is used deliberately: it installs into the active virtual
environment on every OS.

### 3. Create `.env` with fresh secrets and addresses

```bash
python -m scripts.init_env
```

This generates three unique agent seeds, an RSVP signing secret, and the
`SUPPLIER_ADDRESS` / `OUTREACH_ADDRESS` values in `.env`, so there is nothing
to copy by hand. It refuses to overwrite an existing `.env` unless `--force`
is passed (which changes the addresses and requires re-registering the agent).

If you prefer to do it manually, copy `.env.example` to `.env`, fill the three
seeds plus `RSVP_SIGNING_SECRET`, run `python -m scripts.print_addresses`, and
copy the two peer addresses into `.env`. Never commit `.env`.

### 4. Seed and start the stack

```bash
python -m scripts.seed_demo_data
python -m scripts.run_local
```

`run_local` starts all four processes (supplier, outreach, coordinator, RSVP)
and keeps running. Leave it open during the demo.

### 5. Verify in a second terminal

Activate the environment again, then:

```bash
python -m scripts.health_check
```

It checks the three agent endpoints, the RSVP `/health` endpoint, the
databases, and the peer addresses. Exit code `0` means healthy.

| Service | Local URL | Purpose |
|---|---|---|
| Coordinator | `http://127.0.0.1:8101` | ASI:One/uAgents conversation |
| Supplier | `http://127.0.0.1:8102` | Quotes and reservations |
| Outreach | `http://127.0.0.1:8103` | Invitations and RSVP coordination |
| RSVP API | `http://127.0.0.1:8104` | Signed RSVP links and counts |

Stop with `Ctrl+C`. Reset demo state with `python -m scripts.reset_demo`
(add `--owner <sender>` to reset only one organiser). Delete `data/*.db` and
`data/simulated_chain.jsonl` for a completely clean slate.

### Chatting without ASI:One

The coordinator speaks the Agent Chat Protocol on its uAgents endpoint. The
quickest local driver is the Inspector link printed in the coordinator log
(`https://agentverse.ai/inspect/?uri=...&address=...`), which can chat with a
local agent. For a terminal-only run, use two terminals:

```bash
python -m scripts.run_local       # terminal 1
python -m scripts.chat_cli        # terminal 2; reads data/coordinator.address
```

`scripts/chat_cli.py` is a small uAgents client that sends your text to the
coordinator and prints every reply. It uses its own seed and port (8123), so
it does not interfere with the four services.

## Run the judge demo

1. Send the brief above.
2. Complete the form card or answer the missing questions in text.
3. `choose Standard Lab`
4. `approve A-XXXXX` (use the code from the review card/reply)
5. Approve the invitation code shown after payment verification.
6. Use one of the RSVP codes printed in console mode: `RSVP ABC123 yes`.
7. Ask `how many have confirmed?`, then `attendance is now 75` and approve the
   amendment quote to show the budget being replaced, not stacked.

## Endpoint reference

Agent ports are **uAgents transport endpoints**, not REST APIs. They accept
signed uAgents envelopes (and answer the Agentverse Inspector). Opening one in
a browser shows a small JSON status page at `/` (service name, agent address,
where to go next); any other path except `/agent_info` and `/submit` returns
`404 {"error": "not found"}` — that is the agent library, not a fault. The REST
surface is the RSVP service:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness probe; used by platform health checks |
| `GET` | `/ready` | Readiness probe; verifies the RSVP database is available |
| `GET` | `/rsvp/{token}` | Attendee registration page (signed, single-use token) |
| `POST` | `/rsvp/{token}` | Record an RSVP (form-encoded; duplicate-safe) |
| `GET` | `/internal/events/{event_id}/count` | Signed count query (`x-wp-timestamp` + `x-wp-signature` HMAC headers) |

Manual check (any OS; `curl` ships with Windows 10+):

```bash
curl http://127.0.0.1:8104/health
# {"status":"ok","service":"EventPilot-rsvp"}
```

Quick sanity check that the coordinator is listening:

```bash
curl -o NUL -w "%{http_code}\n" http://127.0.0.1:8101/submit   # Windows CMD
curl -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8101/submit  # macOS/Linux/PowerShell
```

A `200` means the agent HTTP server is up. It is normal for `POST /submit`
without a valid signed envelope to return `400`.

## Deploy for Agentverse and ASI:One

### Mental model (read this first)

- **Agentverse registers and fronts your agent; it does not run this Python
  process.** Registration (Almanac) publishes the agent address, manifest and
  profile. Keep `scripts.run_local` (or the Docker container) running.
- **Mailbox mode** is a relay for agents that cannot accept inbound traffic
  (laptop, firewall). Enable it with `AGENT_MAILBOX=true`; no public port is
  required for the coordinator.
- **Public endpoint mode** is for an always-on host. Set
  `AGENT_ENDPOINT=https://your-host/submit` and expose agent port `8101`.
  `AGENT_MAILBOX` and `AGENT_ENDPOINT` are mutually exclusive; the app fails
  fast if both are set.
- **Agentverse hosting (cloud IDE agents) does not apply here**: these are
  three locally running uAgents, not Agentverse Functions. Use mailbox or a
  public endpoint.
- The RSVP service must have a **public URL** for attendee links to work.
  `RSVP_BASE_URL` is embedded in invitation emails.

The detailed, step-by-step guide with screenshots-in-words lives in
[`integrations/agentverse_setup.md`](integrations/agentverse_setup.md).

### Get the address and endpoint

Every agent address is derived from its name and seed:

```bash
python -m scripts.print_addresses
```

The coordinator address is also written to `data/coordinator.address` and
printed at startup. The effective endpoint is one of:

| Mode | Endpoint advertised |
|---|---|
| Local demo (default) | `http://127.0.0.1:8101/submit` |
| `AGENT_MAILBOX=true` | Agentverse relay: `https://agentverse.ai/v2/agents/mailbox/submit` |
| `AGENT_ENDPOINT=...` | exactly the URL you set (must end in `/submit`) |

### Option A — laptop + Agentverse mailbox (fastest for judging)

1. Set `AGENT_MAILBOX=true` in `.env`.
2. Keep the RSVP URL useful: either set `RSVP_BASE_URL` to a tunnel that
   forwards to port 8104, for example
   `cloudflared tunnel --url http://localhost:8104` or `ngrok http 8104`, or
   rely on the console-mode chat RSVP codes (no tunnel required).
3. Restart `python -m scripts.run_local`.
4. In the coordinator log, open the **Agent inspector** URL, click
   **Connect**, choose **Mailbox**, and approve the local-network prompt if the
   browser shows one. Wait for
   `INFO: [mailbox]: Successfully registered as mailbox agent in Agentverse`.
5. Open <https://agentverse.ai> → **My Agents**. The coordinator has a
   **Mailbox** tag. Edit its profile: name/handle, the task-oriented
   description, and the README (published from `readme_path` at startup).
6. From the profile, choose **Chat with Agent** to open ASI:One, or open
   <https://asi1.ai> in a fresh session and paste the demo prompt.
7. Stop the process and send a message: it is not answered until you restart,
   proving the process is the runtime and Agentverse is not hosting it.

The supplier and outreach agents stay on localhost; the coordinator reaches
them inside the same host. Their addresses are unchanged and must match
`SUPPLIER_ADDRESS` / `OUTREACH_ADDRESS`.

### Option B — public host with a stable endpoint (always-on)

Recommended for a VM or Docker host where you can publish ports 8101 and 8104.

1. On the host, clone the repository and create `.env` (`scripts/init_env`).
2. Set:

   ```dotenv
   AGENT_MAILBOX=false
   AGENT_ENDPOINT=https://your-host.example.com/submit
   RSVP_BASE_URL=https://your-host.example.com
   ```

   The coordinator logs a `Public inspector:` line built from this URL; the
   standard Inspector line still points at `127.0.0.1` because that is the
   local listener.

3. Expose **8101** (agent transport, for `AGENT_ENDPOINT`) and **8104**
   (RSVP). Ports 8102/8103 can stay private. Terminate TLS in front of both
   (reverse proxy, load balancer, or platform ingress).
4. Run the stack:

   ```bash
   docker build -t EventPilot .
   docker run -d --name EventPilot \
     --env-file .env \
     -p 8101:8101 -p 8104:8104 \
     -v "$PWD/data:/app/data" \
     EventPilot
   ```

   On Windows PowerShell, replace `"$PWD/data:/app/data"` with
   `"${PWD}/data:/app/data"`; Command Prompt users can omit the volume line
   for an ephemeral demo.
5. Verify from outside the host:

   ```bash
   curl https://your-host.example.com/health
   curl -o /dev/null -w "%{http_code}\n" https://your-host.example.com:8101/submit
   ```

6. uAgents logs the Inspector URL for its local listener. For a hosted agent,
   use the **Public inspector** line the coordinator prints when
   `AGENT_ENDPOINT` is set (or build
   `https://agentverse.ai/inspect/?uri=<url-encoded public base>&address=<agent address>`
   yourself, without the `/submit` suffix). Do not connect a mailbox in this
   mode: the public endpoint is already the route, and a mailbox the process
   is not polling would silently queue messages. Register the profile in
   Agentverse and chat from ASI:One.

`AGENT_ENDPOINT` must be the URL ASI:One can reach; `127.0.0.1` and
`localhost` will fail for remote users. If your platform gives you a single
public port only, run the stack with mailbox mode and expose just the RSVP
service.

### Option C — single-port PaaS (Render, Railway web service, Fly single port)

These platforms expose one HTTP port (`$PORT`). The RSVP service should use
it; the agents connect to Agentverse through the mailbox relay.

1. Set `AGENT_MAILBOX=true`, leave `RSVP_PORT` empty so the platform `$PORT`
   is used, and set `RSVP_BASE_URL=https://<your-service>`.
2. **Claim the mailbox once from your laptop first**, using the same `.env`
   (same seeds → same coordinator address). Mailboxes are attached to the
   agent address, not to the process, so the hosted agent can use it after
   you connect it locally.
3. Deploy the container or use the start command
   `python -m scripts.seed_demo_data && python -m scripts.run_local`.
4. Platform health check path: `/health`. The agent ports 8101–8103 do not
   need to be public in mailbox mode.
5. Confirm the hosted log prints
   `Successfully registered as mailbox agent in Agentverse`, then chat from a
   fresh ASI:One session.

Databases live in `DATABASE_DIR` (`./data`). On platforms with ephemeral
filesystems, attach a persistent volume if you want demo state to survive a
redeploy; otherwise reseeding is automatic on container start.

### Registration details judges need

Fill this after the coordinator is registered:

| Item | Value |
|---|---|
| Coordinator address | `agent1q...` (from `data/coordinator.address`) |
| Coordinator profile | `https://agentverse.ai/agents/<your-agent>` |
| Supplier address | `agent1q...` |
| Outreach address | `agent1q...` |
| RSVP public URL | `https://.../health` |
| Demo video | `<link>` |

Agentverse profile checklist: name/handle, task-oriented description, README
(the repository README works), category/tags, and a test conversation in the
Inspector. Then test from ASI:One in a **fresh session**.

## Tests

```bash
python -m pytest                                  # complete suite
python -m pytest tests/unit                       # deterministic business rules
python -m pytest tests/integration                # workflow + RSVP integration
```

The live agent-identity smoke test is opt-in because it constructs real
uAgents identities:

```bash
# macOS / Linux
RUN_AGENT_TESTS=1 python -m pytest tests/protocol/test_local_agents.py -m agent_network

# Windows PowerShell
$env:RUN_AGENT_TESTS="1"; python -m pytest tests/protocol/test_local_agents.py -m agent_network

# Windows Command Prompt
set RUN_AGENT_TESTS=1 && python -m pytest tests/protocol/test_local_agents.py -m agent_network
```

The golden path is covered by `tests/e2e/test_acceptance.py` using an
in-memory bus, so it does not require live Agentverse, email, or blockchain.
Card-click handling (JSON and planner-mediated) is covered in
`tests/integration/test_workflow.py`.

## Fetch.ai and ASI:One

This project uses:

- `uagents==0.25.5` for separately running coordinator, supplier, and outreach
  agents;
- `uagents-core==0.4.9` for the Agent Chat Protocol (0.3.0) and Interactive
  Cards;
- Agentverse mailbox mode so the coordinator can be reached from ASI:One;
- optional ASI1 access for intent extraction and invitation drafting. Without
  `ASI1_API_KEY`, deterministic templates are used and the user is told so.

Official references: [uAgents docs](https://uagents.fetch.ai/docs),
[mailbox setup](https://uagents.fetch.ai/docs/agentverse/mailbox),
[Agentverse docs](https://agentverse.ai/docs),
[ASI:One docs](https://docs.asi1.ai).

## Configuration

All settings are documented in [`.env.example`](.env.example). Required for a
local run: `COORDINATOR_SEED`, `SUPPLIER_SEED`, `OUTREACH_SEED`,
`SUPPLIER_ADDRESS`, `OUTREACH_ADDRESS`, `RSVP_SIGNING_SECRET`, and
`ALLOWED_TEST_RECIPIENTS`. Defaults are intentionally safe for a hackathon
demo:

- `PAYMENT_MODE=simulated`: file-backed ledger, not blockchain settlement.
- `EMAIL_PROVIDER=console`: masked records, explicitly **NOT delivered**;
  the chat then shows demo RSVP codes so the flow is demonstrable.
- Empty `ASI1_API_KEY`: deterministic fallbacks, no LLM dependency.
- Packages in `shared/inventory.py`: fictional demo inventory.
- `AGENT_MAILBOX=true`: coordinator uses the Agentverse relay (no inbound
  port); `AGENT_ENDPOINT=https://.../submit` for a public endpoint instead.

The INR budget is the event expense. The tFET deposit is a separate symbolic
reservation deposit; no INR/tFET exchange rate exists. Read
[`payments/README.md`](payments/README.md) before enabling testnet mode.

## Troubleshooting

**`python` is not recognized (Windows)** — use `py -3.12` instead, or install
Python from python.org and tick "Add Python to PATH".

**PowerShell refuses to run `Activate.ps1`** — run
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in that terminal
only, then activate again.

**`ModuleNotFoundError: uagents_core`** — the virtual environment is not
active. Activate it (`.venv\Scripts\Activate.ps1` or
`source .venv/bin/activate`), then `python -m pip install -r requirements.txt`.

**`Address already in use` / port already bound** — another stack is running.
Stop it (`Ctrl+C`), or find the process with `lsof -i :8101` (macOS/Linux) or
`netstat -ano | findstr :8101` (Windows) and stop it.

**`Cannot start the stack: ... missing`** — run `python -m scripts.init_env`
(or `--force` to rotate), then `python -m scripts.seed_demo_data`.

**Missing peer addresses** — `scripts.init_env` fills them. If seeds changed,
re-run `python -m scripts.print_addresses` and update `.env`.

**`Agent mailbox not found: create one using the agent inspector`** — the
process is running but no mailbox exists yet. Open the Inspector URL from the
log, click **Connect → Mailbox**, and wait for
`Successfully registered as mailbox agent in Agentverse`.

**`Endpoint configuration overrides mailbox setting`** — this warning means
both were configured. They are now mutually exclusive and the app refuses to
start; unset `AGENT_ENDPOINT` when `AGENT_MAILBOX=true`.

**ASI:One cannot reach the coordinator** — verify the process is alive, the
mailbox line appeared, the address in Agentverse matches
`data/coordinator.address`, and the manifest says `AgentChatProtocol`. Start a
fresh ASI:One session after redeploying.

**RSVP links point to `localhost`** — set `RSVP_BASE_URL` to the public HTTPS
URL of the RSVP service and restart. Chat RSVP codes work without a public URL
in console mode.

**`RSVP_SIGNING_SECRET is required to issue tokens`** — generate one with
`python -m scripts.init_env` or add a 32+ character random value to `.env`.

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
scripts/      init, seed, run, health, reset, chat, and address utilities
tests/        unit, integration, protocol, and acceptance coverage
docs/         engineering handback and operational notes
Dockerfile    single-container deployment for all four services
```

## Known limitations and submission checklist

The current build does not include real venue booking, refunds, live testnet
settlement, or guaranteed SMTP delivery. Live ASI:One card rendering and
Agentverse mailbox reachability must be checked in the deployment environment.

Before submitting to Fetch-A-Thon 3.0:

1. Run and record the local golden-path demo.
2. Register the coordinator in Agentverse (mailbox or public endpoint) and
   verify a fresh ASI:One session, including one card click.
3. Add agent addresses, profile URL, shared chat, RSVP URL, and demo video to
   the submission materials and the table above.
4. State clearly that payments are simulated and inventory is demo data.

## License

MIT — see [`LICENSE`](LICENSE).
