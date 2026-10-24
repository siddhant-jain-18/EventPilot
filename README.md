![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)
![Python](https://img.shields.io/badge/python-3.11--3.13-3776AB)
![License](https://img.shields.io/badge/license-MIT-green)

# EventPilot

**A budget-safe, multi-agent workshop organiser for ASI:One.** Tell it what workshop you want. It compares supplier packages, asks before it commits, spends, or sends anything, tracks RSVPs, and re-plans when attendance changes, without silently raising your budget.

Built for **Fetch-A-Thon 3.0** with `uagents`, the Agent Chat Protocol, Interactive Cards, and Agentverse.

> **Demo status.** Supplier inventory is fictional, payments go through a simulated ledger, and email is printed to the console by default. Nothing here books a real venue, moves funds on-chain, or delivers email unless you explicitly configure it.

**Contents:** [Try it](#try-it) · [Real vs simulated](#whats-real-and-whats-simulated) · [Architecture](#architecture) · [Quick start](#quick-start) · [Deploy](#deploy-to-agentverse-and-asione) · [Configuration](#configuration) · [Tests](#tests) · [Troubleshooting](#troubleshooting) · [Limitations](#known-limitations)

## Try it

In ASI:One (or the local chat client below), send:

> Organise a robotics workshop for 60 people in Jaipur on November 15, 2026. Keep the supplier budget under ₹20,000. Ask me before committing to a package, sending invitations, or spending funds.

Then follow the golden path:

1. Fill the form card (or answer the text questions) for any missing details.
2. Compare the packages: Starter ₹14,500, Standard ₹19,200, Premium ₹26,000 (blocked by the budget).
3. `choose Standard Lab`, then `approve A-XXXXX` using the code shown. Reservation and the symbolic deposit happen only after approval.
4. Approve the invitation, which is bound to the exact content and recipient set.
5. Register an attendee: `RSVP ABC123 yes` in chat, or use the emailed link.
6. Ask `How many have confirmed?`
7. Say `Attendance is now 75`. This creates plan v2, invalidates stale approvals, and requests a new approval. The amendment replaces the earlier commitment, it does not stack on it.

Other commands: `status`, `checklist`, `timeline`, `help`, `reset my demo`.

## What's real and what's simulated

| Capability | Default | Notes |
|---|---|---|
| Agent-to-agent messaging, approvals, budget checks, audit trail | **Real** | Deterministic application code |
| Agent Chat Protocol and Interactive Cards | **Real** | `uagents-core` |
| Supplier inventory and reservations | **Simulated** | Fictional demo packages in `shared/inventory.py` |
| Payment | **Simulated** | File-backed ledger, labelled "SIMULATED — NO ON-CHAIN PAYMENT" |
| Email | **Console** | Not delivered. Chat shows demo RSVP codes. Set SMTP to deliver. |
| Intent extraction and invitation drafting | **Optional** | Uses ASI1 when `ASI1_API_KEY` is set, otherwise templates |

The INR budget is the event expense. The tFET deposit is a separate symbolic reservation deposit. No INR/tFET exchange rate exists.

## Architecture

```text
ASI:One / Agent Chat Protocol
             |
             v
  Coordinator agent (:8101)
    |  quotes / reservation / payment evidence
    +-----------------------> Supplier agent (:8102)
    |
    +  invitation draft / send / RSVP count
              |
              v
       Outreach agent (:8103) --> RSVP API (:8104)
```

| Component | Responsibility | Safety boundary |
|---|---|---|
| Coordinator | Plans, approvals, orchestration, audit | Never spends or sends without a matching approval |
| Supplier | Inventory, quotes, reservations, payment verification | Does not trust the coordinator's payment claim |
| Outreach | Drafts, allowlist-only sends, signed RSVPs | Never exposes attendee emails in chat or URLs |
| RSVP API | Signed registration, deduplicated counts | Scoped, single-use, expiring tokens |

### Safety model
- Agents propose and explain. Deterministic code owns budget checks, approvals, verification, idempotency, and the audit log.
- Approvals bind to exact details: plan version, package, amount, recipients, and content.
- Money is handled as integers.
- Emails go only to an allowlist of test recipients.
- Stale, altered, expired, or replayed approvals and tokens are rejected.

## Fetch-A-Thon 3.0 requirements

| Requirement | Where |
|---|---|
| Agents registered on Agentverse | Coordinator, supplier, outreach: addresses below |
| Agent Chat Protocol | Coordinator (`uagents-core` chat protocol) |
| Discoverable and usable in ASI:One | Mailbox or public endpoint, [agent README](AGENT_README.md) |
| Multi-agent collaboration | Coordinator, supplier, and outreach exchange typed messages |
| Interactive Cards | Form, package comparison, and approval review |
| Complete workflow in ASI:One | Golden path above |

## Quick start

Requirements: Python 3.11–3.13 (3.12 tested) and pip. No Docker, database server, or ASI1 key needed.

```bash
git clone [https://github.com/siddhant-jain-18/EventPilot.git](https://github.com/siddhant-jain-18/EventPilot.git)
cd EventPilot
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m scripts.init_env          # fresh seeds, signing secret, peer addresses
python -m scripts.seed_demo_data
python -m scripts.run_local         # starts all four services; leave it running
```

<details>
<summary>Windows PowerShell / Command Prompt</summary>

```powershell
git clone [https://github.com/siddhant-jain-18/EventPilot.git](https://github.com/siddhant-jain-18/EventPilot.git)
cd EventPilot
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, allow it for this terminal only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Command Prompt: use `.venv\Scripts\activate.bat`. Then run the same `python -m ...` commands as above.
</details>

`scripts.init_env` creates `.env` and refuses to overwrite one unless you pass `--force`. Forcing changes the agent addresses, so you must re-register. Never commit `.env`.

Verify in a second terminal (activate the venv first):

```bash
python -m scripts.health_check      # exit code 0 = healthy
```

| Service | URL | Purpose |
|---|---|---|
| Coordinator | `http://127.0.0.1:8101` | ASI:One / uAgents conversation |
| Supplier | `http://127.0.0.1:8102` | Quotes and reservations |
| Outreach | `http://127.0.0.1:8103` | Invitations and RSVP coordination |
| RSVP API | `http://127.0.0.1:8104` | Signed RSVP links and counts |

Stop with `Ctrl+C`. Reset with `python -m scripts.reset_demo` (add `--owner <sender>` for one organiser), or delete `data/*.db` and `data/simulated_chain.jsonl`.

### Chat without ASI:One
```bash
python -m scripts.run_local       # terminal 1
python -m scripts.chat_cli        # terminal 2 (uses port 8123)
```
You can also chat with the local agent from the Agentverse Inspector link in the coordinator log.

### Endpoint reference
Agent ports are uAgents transport endpoints, not REST APIs. Other paths returning `404` is expected. The REST surface is the RSVP service:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness probe |
| `GET` | `/ready` | Readiness, verifies the RSVP database |
| `GET` / `POST` | `/rsvp/{token}` | Registration page and submission (signed, single-use) |
| `GET` | `/internal/events/{event_id}/count` | Signed count query (`x-wp-timestamp`, `x-wp-signature`) |

```bash
curl http://127.0.0.1:8104/health
# {"status":"ok","service":"EventPilot-rsvp"}
```
On Windows PowerShell use `curl.exe`.

## Deploy to Agentverse and ASI:One

**Agentverse registers and fronts your agents. It does not run this Python process.** Keep `scripts.run_local` (or the container) running, because the agent goes inactive when the process stops. This stack uses local uAgents, not Agentverse-hosted agents.

| Mode | Use when | Setting |
|---|---|---|
| Mailbox | Laptop or firewalled host, fastest for judging | `AGENT_MAILBOX=true` |
| Public endpoint | Always-on host with ports you control | `AGENT_ENDPOINT=https://your-host/submit` |

`AGENT_MAILBOX` and `AGENT_ENDPOINT` are mutually exclusive. The app refuses to start if both are set.

### Option A: mailbox from a laptop
1. Set `AGENT_MAILBOX=true` in `.env`.
2. For RSVP links, set `RSVP_BASE_URL` to a tunnel (`cloudflared tunnel --url http://localhost:8104` or `ngrok http 8104`). Chat RSVP codes work without one.
3. Restart `python -m scripts.run_local`.
4. Open the **Inspector** URL from the coordinator log, click **Connect**, choose **Mailbox**, and allow the local-network prompt in Chrome or Brave.
5. Wait for `Successfully registered as mailbox agent in Agentverse`.
6. In Agentverse, open **My Agents**, find the Mailbox tag, and complete the profile (handle, description, README).
7. Open ASI:One in a fresh session and paste the demo prompt.

The supplier and outreach agents stay on localhost and must keep the addresses in `.env`.

### Option B: public host (always-on)
1. Clone the repo and run `python -m scripts.init_env` on the host.
2. Set:
   ```dotenv
   AGENT_MAILBOX=false
   AGENT_ENDPOINT=https://your-host.example.com/submit
   RSVP_BASE_URL=https://your-host.example.com
   ```
3. Put a TLS reverse proxy in front: `/submit` and `/agent_info` to port 8101, and `/rsvp/*`, `/health`, `/ready` to port 8104. Ports 8102 and 8103 stay private.
4. Run the container:
   ```bash
   docker build -t eventpilot .
   docker run -d --name eventpilot --env-file .env \
     -p 8101:8101 -p 8104:8104 \
     -v "$PWD/data:/app/data" eventpilot
   ```
   On PowerShell use `"${PWD}/data:/app/data"`.
5. Verify from outside:
   ```bash
   curl https://your-host.example.com/health
   curl https://your-host.example.com/agent_info
   ```
6. Do not connect a mailbox in this mode. Register the profile in Agentverse and chat from ASI:One.

`AGENT_ENDPOINT` must be reachable by ASI:One, so `localhost` and `127.0.0.1` will not work.

### Option C: single-port PaaS
For platforms that expose only `$PORT`, use mailbox mode and let the RSVP service take the platform port.
1. Set `AGENT_MAILBOX=true`, leave `RSVP_PORT` empty, and set `RSVP_BASE_URL` to the service URL.
2. Claim the mailbox once from your laptop with the same `.env` (same seeds, same address), then deploy.
3. Use `/health` as the health check and confirm the hosted log shows the mailbox registration.
4. Attach a persistent volume for `DATABASE_DIR` if demo state must survive redeploys. Otherwise the container reseeds on start.

Details live in [`integrations/agentverse_setup.md`](integrations/agentverse_setup.md). Free tiers that sleep when idle will take your agents offline, so use an always-on service type.

### Registration details

| Item | Value |
|---|---|
| Coordinator address | `agent1q...` (see `data/coordinator.address`) |
| Coordinator profile | `https://agentverse.ai/agents/<...>` |
| Supplier address | `agent1q...` |
| Outreach address | `agent1q...` |
| RSVP public URL | `https://.../health` |
| Shared ASI:One chat | `<link>` |
| Demo video | `<link>` |

The Agentverse profile shows [`AGENT_README.md`](AGENT_README.md), published from `readme_path` at startup. Restart the agent after editing it.

## Configuration

Every setting is documented in [`.env.example`](.env.example). Required for a local run: `COORDINATOR_SEED`, `SUPPLIER_SEED`, `OUTREACH_SEED`, `SUPPLIER_ADDRESS`, `OUTREACH_ADDRESS`, `RSVP_SIGNING_SECRET`, `ALLOWED_TEST_RECIPIENTS`.

Safe defaults for a demo:
- `PAYMENT_MODE=simulated` (file-backed ledger). Read [`payments/README.md`](payments/README.md) before enabling testnet mode.
- `EMAIL_PROVIDER=console` (masked records, not delivered).
- Empty `ASI1_API_KEY` (deterministic templates, no LLM dependency).

Pinned versions: `uagents==0.25.5` and `uagents-core==0.4.9` (Agent Chat Protocol 0.3.0).

## Tests

```bash
python -m pytest                    # full suite
python -m pytest tests/unit         # business rules
python -m pytest tests/integration  # workflow and RSVP
```

Live agent-identity tests are opt-in:
```bash
RUN_AGENT_TESTS=1 python -m pytest tests/protocol/test_local_agents.py -m agent_network   # macOS/Linux
$env:RUN_AGENT_TESTS="1"; python -m pytest tests/protocol/test_local_agents.py -m agent_network   # PowerShell
```

The golden path is covered by `tests/e2e/test_acceptance.py` on an in-memory bus, so it needs no live Agentverse, email, or blockchain.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `python` not recognized (Windows) | Use `py -3.12`, or reinstall Python with "Add to PATH" |
| PowerShell blocks `Activate.ps1` | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` |
| `ModuleNotFoundError: uagents_core` | Activate the venv, then `python -m pip install -r requirements.txt` |
| Port already in use | Stop the other stack, or find it with `lsof -i :8101` / `netstat -ano \| findstr :8101` |
| `Cannot start the stack: ... missing` | Run `python -m scripts.init_env`, then `python -m scripts.seed_demo_data` |
| Missing peer addresses | Run `python -m scripts.print_addresses` and update `.env` |
| `Agent mailbox not found` | Open the Inspector link, **Connect → Mailbox**, wait for the registration line |
| `Endpoint configuration overrides mailbox setting` | Unset `AGENT_ENDPOINT` when `AGENT_MAILBOX=true` |
| ASI:One cannot reach the agent | Check the process is alive, the mailbox line appeared, the address matches `data/coordinator.address`, and the manifest lists `AgentChatProtocol`. Start a fresh ASI:One session. |
| RSVP links point to `localhost` | Set `RSVP_BASE_URL` to the public HTTPS URL and restart |
| `RSVP_SIGNING_SECRET is required` | Run `python -m scripts.init_env` or set a 32+ character random value |
| Payment says "simulated" | Expected. This build never claims an on-chain transfer |
| A test appears to hang | Run unit tests first, then integration tests separately |

## Repository map

```text
agents/       coordinator, supplier, outreach uAgents
services/     planning, quotes, approvals, replanning, audit
shared/       models, contracts, validation, cards, configuration
payments/     simulated ledger and independent verification
storage/      SQLite schemas and repositories
rsvp_web/     FastAPI RSVP service
scripts/      init, seed, run, health, reset, chat, address utilities
tests/        unit, integration, protocol, acceptance
Dockerfile    single container for all four services
```

## Known limitations

- No real venue booking, refunds, live testnet settlement, or guaranteed SMTP delivery.
- Live ASI:One card rendering and mailbox reachability must be checked in your own deployment.
- Demo inventory and prices are fictional.

## License

MIT, see [`LICENSE`](LICENSE).