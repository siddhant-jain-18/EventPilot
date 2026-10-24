# Agentverse & ASI:One deployment guide

This guide gets WorkshopPilot from a local checkout to an agent that judges can
chat with in ASI:One. For a local-only demo, the [root README](../README.md) is
enough.

**Read this first:** Agentverse registers and fronts your agent; it does not
host this Python process. Three uAgents processes (coordinator, supplier,
outreach) plus the RSVP web service must run somewhere. Mailbox mode is a relay
for agents that cannot accept inbound connections. A public endpoint is the
alternative for an always-on host. The RSVP service needs a public URL if
attendees should click invitation links.

## 0. Prerequisites

- Python 3.11+ (3.12 tested) on macOS, Linux, or Windows.
- An Agentverse account at <https://agentverse.ai> and an ASI:One account at
  <https://asi1.ai> (use the same login).
- A `.env` generated from this repository. Do **not** reuse seeds from any
  other project: the address is derived from the seed.

```bash
python -m scripts.init_env
python -m scripts.seed_demo_data
python -m scripts.health_check
```

If `init_env` refuses because `.env` exists, keep the existing file. Changing
seeds later changes every agent address and requires re-registering.

## 1. Choose a deployment mode

| Mode | When to use | Required settings |
|---|---|---|
| **Mailbox** (recommended for a laptop) | No public IP, firewall, judging from your machine | `AGENT_MAILBOX=true`; RSVP public URL only if attendees should click links |
| **Public endpoint** | Always-on VM or container with a stable HTTPS address | `AGENT_MAILBOX=false`, `AGENT_ENDPOINT=https://host/submit`, expose port 8101 |
| **Single-port PaaS** | Render/Railway web service (one public port) | `AGENT_MAILBOX=true`, `RSVP_PORT` empty, claim the mailbox once locally |

`AGENT_MAILBOX` and `AGENT_ENDPOINT` are mutually exclusive. uAgents silently
lets an explicit endpoint override mailbox mode; this repository now refuses
to start in that state, so check the error if you set both.

The coordinator is the only agent ASI:One talks to. Supplier and outreach stay
on localhost inside the same host/container and are reached through
`SUPPLIER_ADDRESS` / `OUTREACH_ADDRESS`.

## 2. Start the stack

```bash
python -m scripts.run_local
```

Expected coordinator log lines:

```text
Starting agent with address: agent1q...
Agent inspector available at https://agentverse.ai/inspect/?uri=...&address=...
Starting server on http://0.0.0.0:8101
Manifest published successfully: AgentChatProtocol
```

With mailbox mode you should also see:

```text
Starting mailbox client for https://agentverse.ai
```

The first time, Agentverse will say `Agent mailbox not found: create one using
the agent inspector`. That is the next step.

## 3A. Connect the mailbox (laptop / firewalled host)

1. Keep `scripts.run_local` running.
2. Open the **Agent inspector** URL printed by the coordinator.
3. Click **Connect** and choose **Mailbox**.
4. If the browser asks for Local Network Access permission (Chrome/Brave
   recent versions), allow it; otherwise the Inspector cannot see the local
   agent.
5. Wait for the coordinator log to print:

   ```text
   INFO: [mailbox]: Successfully registered as mailbox agent in Agentverse
   INFO: [mailbox]: Agent details updated in Agentverse
   ```

6. Open <https://agentverse.ai> → **My Agents**. The coordinator appears with
   a **Mailbox** tag.

Mailbox messages are stored while the process is offline, but **the process
must be running to answer**. Stopping `scripts.run_local` makes the agent deaf,
not deleted.

## 3B. Public endpoint (always-on host)

Use this when you host the stack on a VM or container and want a stable address
instead of a mailbox.

1. Set the public URLs (replace host and scheme with yours):

   ```dotenv
   AGENT_MAILBOX=false
   AGENT_ENDPOINT=https://your-host.example.com/submit
   RSVP_BASE_URL=https://your-host.example.com
   ```

2. Expose **8101** (agent transport) and **8104** (RSVP). Ports 8102/8103 can
   remain private.
3. Run the container:

   ```bash
   docker build -t workshoppilot .
   docker run -d --name workshoppilot \
     --env-file .env \
     -p 8101:8101 -p 8104:8104 \
     -v "$PWD/data:/app/data" \
     workshoppilot
   ```

   (PowerShell: use `"${PWD}/data:/app/data"`; omit the volume line for an
   ephemeral demo.)
4. Verify from outside the host:

   ```bash
   curl https://your-host.example.com/health
   curl -o /dev/null -w "%{http_code}\n" https://your-host.example.com:8101/submit
   ```

5. uAgents prints the Inspector URL for the local listener even when a public
   endpoint is configured. Open the **Public inspector** line that the
   coordinator logs when `AGENT_ENDPOINT` is set, or build it yourself:
   `https://agentverse.ai/inspect/?uri=<url-encoded public base>&address=<agent address>`
   (strip the trailing `/submit` from the URI). Do **not** connect a mailbox
   in this mode: the public endpoint is already the route, and a mailbox that
   the process is not polling would silently queue messages.
6. Register the profile in Agentverse (step 4) and chat from ASI:One.

The endpoint must be reachable from the internet. `127.0.0.1`, `localhost`,
and private LAN addresses will fail for ASI:One. Keep TLS termination in front
of both ports.

## 3C. Single-port PaaS (Render / Railway web service)

These platforms expose one HTTP port via `$PORT`.

1. Set `AGENT_MAILBOX=true`, leave `RSVP_PORT` empty (the code falls back to
   `PORT`, then 8104), and set `RSVP_BASE_URL=https://<your-service>`.
2. Claim the mailbox once **from your laptop** with the same `.env` (same
   seeds = same address): run locally, open the Inspector, Connect → Mailbox.
   Mailboxes belong to the agent address, not to the process, so the deployed
   agent will claim the same mailbox. Stop the local process before starting
   the hosted one so only one process polls the mailbox.
3. Deploy the Dockerfile or use the start command:

   ```bash
   python -m scripts.seed_demo_data && python -m scripts.run_local
   ```

4. Platform health check path: `/health`.
5. In the deployed log, confirm `Successfully registered as mailbox agent in
   Agentverse`, then test from a fresh ASI:One session.

If the platform restarts the container, SQLite state is lost unless you attach
a persistent volume at `DATABASE_DIR` (`./data`). Seeding runs automatically on
container start.

## 4. Agentverse profile

For the coordinator:

1. Agentverse → **My Agents** → the coordinator (Mailbox tag or public
   endpoint).
2. Set a name and handle, e.g. `workshoppilot_coordinator`.
3. Use a task-oriented description: "Plan a workshop, compare supplier
   packages, approve a budget, send invitations and track RSVPs."
4. The README is published from `readme_path="README.md"` at startup. Keep the
   Fetch-A-Thon badges at the top; they render on the profile.
5. Use the **Testing** tab (Manual Test / Generate Test) with the demo prompt
   from the root README.
6. Record the profile URL and agent address for the submission table.

The supplier and outreach agents do not need public profiles for judging, but
their addresses must stay stable and match `.env`.

## 5. Test from ASI:One

Open <https://asi1.ai> in a **fresh session** (cold start) and test in order:

1. `what can you do?` → capability summary plus the demo-data disclaimer.
2. The full brief from the README → form card (or text questions), quotes, and
   a review card.
3. Click a package button, then approve with the code from the reply. Confirm
   the payment line says **SIMULATED — NO ON-CHAIN PAYMENT**.
4. Approve the invitation. In console mode the reply lists single-use RSVP
   codes; with SMTP the link and code arrive by email.
5. `RSVP ABC123 yes` (twice) → the second reply says it was already used and
   the count stays at 1.
6. `attendance is now 75` → plan v2, stale approvals invalidated, fresh quote
   needed.
7. `tell me a joke` → graceful redirect, no stack trace.
8. Open a second browser profile → a different sender must not see the first
   organiser's event.

Card clicks can arrive three ways, all handled: a validated
`MetadataContent` card response, a JSON text block (direct @mention), or prose
from the planner that names the approval code. Plain text commands always work
as a fallback.

## 6. Keeping the demo alive

- Judge windows are long; a laptop that sleeps will drop the mailbox
  connection. Use `tmux`, `screen`, `systemd`, or a small VM for the judging
  window.
- Logs are the source of truth: check for `Manifest published successfully`,
  `Successfully registered as mailbox agent`, and no repeated
  `Failed to connect` lines.
- Rotating seeds invalidates the Agentverse registration and any copied
  addresses. Re-run `scripts.print_addresses` and re-register.
- `python -m scripts.health_check` verifies all four HTTP listeners locally.

## 7. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Agent mailbox not found: create one using the agent inspector` | Process is fine, mailbox missing. Open the Inspector → Connect → Mailbox. |
| `Endpoint configuration overrides mailbox setting` | Both `AGENT_ENDPOINT` and `AGENT_MAILBOX=true` are set. Unset one; startup now fails fast with this message. |
| ASI:One gets no reply | Process not running, mailbox not connected, or wrong address in the profile. Check the log and `data/coordinator.address`. |
| Inspector cannot see the local agent | Allow Local Network Access in Chrome/Brave site settings, or open the **Public inspector** URL logged when `AGENT_ENDPOINT` is set. |
| `curl: (7) Failed to connect` to `:8101` | The coordinator is not running or the port is not exposed/published. `scripts.health_check` shows which listener is missing. |
| Invitation links point to `localhost` | Set `RSVP_BASE_URL` to the public RSVP URL. Chat RSVP codes still work in console mode. |
| Agentverse profile README is stale | Restart the coordinator: `readme_path` is published on startup. |
| Address changed after redeploy | Seeds changed. Keep the same `.env`, or update `SUPPLIER_ADDRESS`/`OUTREACH_ADDRESS`, Agentverse registration, and the submission table. |

## 8. Submission checklist

- [ ] `python -m pytest` passes on the submitting machine.
- [ ] Local golden-path demo recorded (3–5 minutes).
- [ ] Coordinator registered in Agentverse and reachable from a fresh ASI:One
      session, including one card click.
- [ ] Agent address, profile URL, RSVP public URL, shared chat, and video added
      to the README table and Devpost submission.
- [ ] Payment mode and demo inventory clearly labelled as simulated/fictional.
