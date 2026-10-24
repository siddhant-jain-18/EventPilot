# Agentverse & ASI:One setup guide

This guide registers the three WorkshopPilot agents so the coordinator is
reachable from ASI:One. Registration does NOT host your code — the processes
must run somewhere (laptop, VM, or container) for the demo. Verify every step
against the current Agentverse docs; UI labels change.

## 0. Prerequisites

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in the seeds and settings
```

## 1. Generate unique seeds

Each agent needs its OWN seed (never reuse across agents or environments).
Put 32+ random characters into `COORDINATOR_SEED`, `SUPPLIER_SEED`,
`OUTREACH_SEED` in `.env`. Example generation:

```bash
python3 -c "import secrets; print(secrets.token_hex(24))"
```

## 2. Compute agent addresses

uAgents addresses are derived deterministically from (name, seed):

```bash
python3 scripts/print_addresses.py
```

Put the printed SUPPLIER and OUTREACH addresses into `SUPPLIER_ADDRESS` and
`OUTREACH_ADDRESS` (the coordinator needs them to initiate requests), and set
the same agent names/seeds consistently — changing either changes the address.

## 3. Local run (mailbox mode)

```bash
python3 scripts/seed_demo_data.py
python3 scripts/run_local.py
```

`run_local.py` starts supplier (:8102), outreach (:8103), coordinator (:8101)
and the RSVP service (:8104). With `AGENT_MAILBOX=true` the coordinator
connects through the Agentverse mailbox relay so ASI:One can reach it; the
supplier/outreach agents stay on local endpoints by default.

Verification:

```bash
python3 scripts/health_check.py
```

## 4. Register on Agentverse (manual steps)

For EACH agent (do this for all three; each submitted agent needs a profile):

1. Sign in at https://agentverse.ai with the same account you use in ASI:One.
2. Agentverse -> My Agents / Mailbox -> register a mailbox agent using the
   agent's address (the local process must be running so it can complete the
   mailbox handshake).
3. Open the agent profile and fill in:
   - handle/name (e.g. `workshoppilot_coordinator`),
   - a task-oriented description: "Organise a workshop, compare packages,
     track RSVPs, and re-plan within a budget",
   - the README (the repository README works; keep the innovationlab +
     hackathon badges at the top).
4. Use the Agentverse Testing tab (Manual Test / Generate Test) and check the
   agent answers the example prompts from the README.
5. Record the profile URLs and agent addresses in the README
   ("Agent addresses" section) — judges need them.

## 5. Test from ASI:One

Open https://asi1.ai in a FRESH session (cold start) and try, in order:

1. a greeting ("what can you do?") -> capability summary + demo-data disclaimer,
2. the full cold-start prompt from the README,
3. an off-topic message ("tell me a joke") -> graceful redirect, no stack trace,
4. a second browser profile -> state must be isolated per sender.

## 6. Keeping the demo alive

- Mailbox mode buffers messages while the process is offline, but it does NOT
  run the process. Keep `run_local.py` alive for the whole judging window
  (screen/tmux/systemd on a small VM is the reliable option).
- If you redeploy with different seeds, the addresses change — re-register and
  update the README.
