"""Agentverse registration helper for the EventPilot coordinator.

This is a one-shot helper meant to run on a **trusted host that possesses the
coordinator agent's private key**. The Agentverse v1 API now uses a signed
challenge/response flow:

  1. POST https://agentverse.ai/v1/auth/challenge  with {"address": <addr>}
     using the AGENTVERSE_API_KEY bearer token.
  2. POST https://agentverse.ai/v1/agents with the agent payload, the challenge
     JWT, and a signature over the challenge produced with the **agent's private
     key** (the same seed the coordinator is started from).

The key must be the **exact same Ed25519/secp256k1 identity the coordinator
uses**, i.e. derived with uagents' own Identity.from_seed(seed, index=0).
Re-deriving a different curve (for example P-256 via HKDF) produces a different
public key and will be rejected by the verifier, which checks the signature
against the address embedded in the challenge JWT.

A register_only mode is also provided for the v2 endpoint so the script can be
used for debugging / future compatibility.

Required environment variables (at minimum for register mode):
  AGENTVERSE_API_KEY
  AGENTVERSE_API_URL (optional; defaults to https://agentverse.ai/v2)
  AGENT_ADDRESS / COORDINATOR_ADDRESS  (defaults to the deployed coordinator address)
  AGENT_ENDPOINT  (defaults to the deployed /submit URL)

For register mode you additionally need one of:
  AGENT_SEED               (hex seed string, same as COORDINATOR_SEED)

Optional:
  AGENT_NAME (defaults to eventpilot_coordinator)
  AGENTVERSE_REGISTER_MODE = register | register_only | diagnose (default: diagnose)
  AGENTVERSE_CHALLENGE_ATTEMPTS (default 5)
  AGENTVERSE_REGISTER_ATTEMPTS  (default 5)

The script prints what it tried and the exact HTTP responses. It never logs the
token or the private key.

Notes
-----
- The coordinator itself cannot complete the signed challenge at startup because
  it does not load the agent private key. The repository therefore skips the v1
  registration at startup (see agents/coordinator/agent.py) and relies on the
  uAgents Almanac/API registration plus the mailbox relay or public endpoint for
  the visible Agentverse listing. This helper is the path for a secure,
  key-holding host to finish the API-side registration.
- If you do not have the agent private key anywhere, use the mailbox flow
  (AGENT_MAILBOX=true + connect the mailbox from the Agentverse inspector) or
  the public-endpoint flow, both of which do not require signing the challenge.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
from typing import Any

import httpx

# Populated by _load_agent_identity() when a seed is available.
_UAGENTS_ADDRESS: str = ""
_UAGENTS_PUB_KEY_HEX: str = ""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _load_agent_identity() -> Any:
    """Return a uagents Core Identity for signing the challenge.

    The identity must match the coordinator's own identity exactly. We recreate
    it with uagents_core.identity.Identity.from_seed(seed, index=0), which is
    the same call the coordinator Agent uses internally. Using the seed to derive
    a *different* curve (for example P-256 via HKDF) produces a different public
    key and will be rejected by the Agentverse verifier.

    Returns the Identity object (used for sign_b64). Also sets module-level
    _UAGENTS_ADDRESS and _UAGENTS_PUB_KEY_HEX so the rest of the script can
    print diagnostics without re-deriving.
    """
    global _UAGENTS_ADDRESS, _UAGENTS_PUB_KEY_HEX

    seed = _env("AGENT_SEED")
    if not seed:
        raise SystemExit(
            "No agent private key available for the signed challenge.\n"
            "Set AGENT_SEED (matching COORDINATOR_SEED).\n"
            "If you do not have the agent private key, use the mailbox flow "
            "(AGENT_MAILBOX=true + connect mailbox from the Agentverse inspector) "
            "or the public-endpoint flow instead of this script."
        )

    try:
        from uagents_core.identity import Identity
    except ImportError as exc:
        raise SystemExit(
            f"uagents_core is required for signed registration but could not be imported: {exc}\n"
            "Install it with: pip install uagents-core\n"
        )

    identity = Identity.from_seed(seed, index=0)
    _UAGENTS_ADDRESS = identity.address
    _UAGENTS_PUB_KEY_HEX = identity.pub_key
    return identity


def _sign_challenge_b64(identity: Any, data: str) -> str:
    """Sign *data* with the uagents Core Identity and return base64 (no padding).

    This replicates exactly what Identity.sign_b64 does internally: it signs with
    sigencode_string_canonize and base64-encodes the 64-byte raw signature.
    """
    from ecdsa.util import sigencode_string_canonize
    raw = identity._sk.sign(data.encode("utf-8"), sigencode=sigencode_string_canonize)
    return base64.b64encode(raw).decode()


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _decode_jwt_part(token: str, part_index: int) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        raise ValueError("not a JWT")
    part = parts[part_index]
    pad = part + "=" * ((4 - len(part) % 4) % 4)
    return json.loads(base64.urlsafe_b64decode(pad))


def fetch_challenge(client: httpx.Client, base_url: str, token: str, address: str, attempts: int) -> str:
    # The live Agentverse v1 challenge endpoint is served at a fixed path; use
    # it directly rather than appending /v1 to base_url (which can 307 when
    # base_url carries a /v2 prefix).
    url = "https://agentverse.ai/v1/auth/challenge"
    for i in range(attempts):
        r = client.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json={"address": address},
            timeout=30,
        )
        if r.status_code == 200:
            payload = r.json()
            challenge = payload.get("challenge")
            if challenge:
                return challenge
        print(
            f"[challenge] attempt {i} -> {r.status_code} {r.text[:200]}",
            file=sys.stderr,
        )
        time.sleep(1)
    raise SystemExit("Could not obtain Agentverse challenge after retries")


def register_v1(
    client: httpx.Client,
    base_url: str,
    token: str,
    payload: dict[str, Any],
    challenge: str,
    identity: Any,
    attempts: int,
) -> httpx.Response:
    # Sign the raw challenge JWT bytes with the uagents identity. The Agentverse
    # verifier reconstructs the agent's verifying key from the address carried in
    # the challenge JWT ("sub") and checks the signature over the challenge.
    print(
        f"[register_v1] agent address : {_UAGENTS_ADDRESS}",
        f"agent pub_key (compressed, hex): {_UAGENTS_PUB_KEY_HEX}",
        f"challenge (first 80): {challenge[:80]}",
        sep="\n",
        file=sys.stderr,
    )
    sig = _sign_challenge_b64(identity, challenge)
    print(f"[register_v1] challenge_response (base64): {sig}", file=sys.stderr)

    body: dict[str, Any] = dict(payload)
    body["challenge"] = challenge
    body["challenge_response"] = sig

    last: httpx.Response | None = None
    for i in range(attempts):
        r = client.post(
            "https://agentverse.ai/v1/agents",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
            timeout=30,
            follow_redirects=False,
        )
        print(f"  [v1] attempt {i} -> {r.status_code} {r.text[:300]}", file=sys.stderr)
        if r.status_code in (200, 201):
            print("  AGENTVERSE REGISTRATION ACCEPTED", file=sys.stderr)
            return r
        last = r
        time.sleep(1)
    return last


def register_v2(client: httpx.Client, base_url: str, token: str, payload: dict[str, Any]) -> httpx.Response:
    r = client.post(
        f"{base_url}/v2/agents",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    return r


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    token = _env("AGENTVERSE_API_KEY")
    if not token:
        print("AGENTVERSE_API_KEY is required", file=sys.stderr)
        return 2

    address = (
        _env("AGENT_ADDRESS")
        or _env("COORDINATOR_ADDRESS")
        or "agent1qf04lg3ffym4s4ghm8herjl5dvgvfyy33303wgk6a27tzadkk39rcqyf784"
    )
    base_url = _env("AGENTVERSE_API_URL", "https://agentverse.ai/v2").rstrip("/")
    name = _env("AGENT_NAME", "eventpilot_coordinator")
    endpoint = _env("AGENT_ENDPOINT", "https://eventpilot-8pjc.onrender.com/submit")

    if not endpoint.rstrip("/").endswith("/submit"):
        print(
            "AGENT_ENDPOINT must end with /submit (it is the uAgents envelope endpoint)",
            file=sys.stderr,
        )
        return 2

    mode = _env("AGENTVERSE_REGISTER_MODE", "diagnose").lower()
    challenge_attempts = int(_env("AGENTVERSE_CHALLENGE_ATTEMPTS", "5"))
    register_attempts = int(_env("AGENTVERSE_REGISTER_ATTEMPTS", "5"))

    payload = {
        "address": address,
        "name": name,
        "agent_type": "uagent",
        "url": endpoint,
        "endpoints": [
            {
                "url": endpoint,
                "weight": 1,
            }
        ],
    }
    payload_minimal = {
        "address": address,
        "name": name,
        "agent_type": "uagent",
        "url": endpoint,
    }

    print("Agentverse registration helper:", file=sys.stderr)
    print(f"  mode              : {mode}", file=sys.stderr)
    print(f"  base_url          : {base_url}", file=sys.stderr)
    print(f"  agent             : {address}", file=sys.stderr)
    print(f"  endpoint          : {endpoint}", file=sys.stderr)
    print(f"  token set         : {bool(token)}", file=sys.stderr)
    print(f"  agent key source  : AGENT_SEED={bool(_env('AGENT_SEED'))} "
          f"AGENT_PRIVATE_KEY_PEM={bool(_env('AGENT_PRIVATE_KEY_PEM'))} "
          f"AGENT_PRIVATE_KEY_UARTS={bool(_env('AGENT_PRIVATE_KEY_UARTS'))}",
          file=sys.stderr,
        )

    client = httpx.Client(http2=True, timeout=30)

    if mode == "diagnose":
        print("\n=== diagnose: probing the live Agentverse API ===", file=sys.stderr)
        diagnose(client, base_url, token, address, payload)
        return 0

    if mode == "register_only":
        print("\n=== register_only: attempting v2 POST (full payload) via /v2/agents ===", file=sys.stderr)
        r = register_v2(client, base_url, token, payload)
        print(f"HTTP {r.status_code} (full payload, via base_url {base_url})", file=sys.stderr)
        print(r.text[:800], file=sys.stderr)
        v2_full_ok = not r.is_error
        if not v2_full_ok:
            print("\n=== register_only: retry with minimal payload (no endpoints) via /v2/agents ===", file=sys.stderr)
            r2 = register_v2(client, base_url, token, payload_minimal)
            print(f"HTTP {r2.status_code} (minimal payload, via base_url {base_url})", file=sys.stderr)
            print(r2.text[:800], file=sys.stderr)
            if not r2.is_error:
                r, v2_full_ok = r2, True
        if not v2_full_ok:
            print("\n=== register_only: retry v2 POST against https://agentverse.ai/v2/agents (no base_url prefix) ===", file=sys.stderr)
            r3 = client.post(
                "https://agentverse.ai/v2/agents",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
                timeout=30,
            )
            print(f"HTTP {r3.status_code} (full payload, bare v2)", file=sys.stderr)
            print(r3.text[:800], file=sys.stderr)
            if not r3.is_error:
                r, v2_full_ok = r3, True
            else:
                print("\n=== register_only: retry v1 POST against /v1/agents (challenge signed with uagents identity) ===", file=sys.stderr)
                challenge = fetch_challenge(client, base_url, token, address, challenge_attempts)
                print(f"  challenge obtained: {challenge[:50]}...", file=sys.stderr)
                identity = _load_agent_identity()
                r4 = register_v1(client, base_url, token, payload, challenge, identity, register_attempts)
                print(f"HTTP {r4.status_code} (v1 full)", file=sys.stderr)
                print(r4.text[:800], file=sys.stderr)
                if not r4.is_error:
                    print("Agentverse registration accepted (via v1).", file=sys.stderr)
                    return 0
        if v2_full_ok:
            print("v2 register accepted.", file=sys.stderr)
            return 0
        print("v2 register rejected.", file=sys.stderr)
        return 1

    if mode == "register":
        print("\n=== register: v1 challenge/response flow ===", file=sys.stderr)
        if not _env("AGENT_SEED"):
            print(
                "AGENTVERSE_REGISTER_MODE=register requires the agent private key.\n"
                "Set AGENT_SEED (matching COORDINATOR_SEED).",
                file=sys.stderr,
            )
            return 2
        identity = _load_agent_identity()
        print(f"  uagents identity address: {_UAGENTS_ADDRESS}", file=sys.stderr)
        print(f"  uagents identity pub_key : {_UAGENTS_PUB_KEY_HEX}", file=sys.stderr)
        print(f"  requested agent address   : {address}", file=sys.stderr)
        if _UAGENTS_ADDRESS != address:
            print(
                f"\n  MISMATCH: AGENT_SEED produces address {_UAGENTS_ADDRESS}, "
                f"but AGENT_ADDRESS/COORDINATOR_ADDRESS is {address}. "
                f"The seed in AGENT_SEED does not match the configured coordinator address. "
                f"Registration cannot succeed until they match.",
                file=sys.stderr,
            )
            return 2
        challenge = fetch_challenge(client, base_url, token, address, challenge_attempts)
        print(f"  challenge obtained: {challenge[:50]}...", file=sys.stderr)
        r = register_v1(client, base_url, token, payload, challenge, identity, register_attempts)
        if r is not None and not r.is_error:
            print(f"HTTP {r.status_code}", file=sys.stderr)
            print(r.text[:800], file=sys.stderr)
            print("Agentverse registration accepted (via v1 challenge).", file=sys.stderr)
            return 0
        v1_status = r.status_code if r else None
        print(f"\n=== v1 challenge register returned HTTP {v1_status}; retrying via v2 register_only ===", file=sys.stderr)
        r2 = register_v2(client, base_url, token, payload)
        print(f"v2 (full payload) HTTP {r2.status_code}", file=sys.stderr)
        print(r2.text[:800], file=sys.stderr)
        if r2.is_error:
            print("\n=== v2 (full) rejected; retrying with minimal payload ===", file=sys.stderr)
            r2 = register_v2(client, base_url, token, payload_minimal)
            print(f"v2 (minimal payload) HTTP {r2.status_code}", file=sys.stderr)
            print(r2.text[:800], file=sys.stderr)
            if r2.is_error:
                print("v2 register rejected.", file=sys.stderr)
                return 1
        print("Agentverse registration accepted (via v2 register_only).", file=sys.stderr)
        return 0

    print(f"Unknown AGENTVERSE_REGISTER_MODE: {mode}", file=sys.stderr)
    return 2


def diagnose(client: httpx.Client, base_url: str, token: str, address: str, payload: dict[str, Any]) -> None:
    print("\n--- 1) anonymous probe of /v2/agents (no token) ---", file=sys.stderr)
    r = client.post(f"{base_url}/agents", json=payload, timeout=30)
    print(f"HTTP {r.status_code} {r.text[:200]}", file=sys.stderr)

    print("\n--- 2) probe with token (v2 POST /agents) ---", file=sys.stderr)
    r = client.post(
        f"{base_url}/agents",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=30,
    )
    print(f"HTTP {r.status_code} {r.text[:300]}", file=sys.stderr)

    print("\n--- 3) probe token on /v1/agents (full payload, no challenge) ---", file=sys.stderr)
    r = client.post(
        "https://agentverse.ai/v1/agents",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    print(f"HTTP {r.status_code} {r.text[:300]}", file=sys.stderr)

    print("\n--- 4) fetch challenge from /v1/auth/challenge ---", file=sys.stderr)
    r = client.post(
        "https://agentverse.ai/v1/auth/challenge",
        headers={"Authorization": f"Bearer {token}"},
        json={"address": address},
        timeout=30,
    )
    print(f"HTTP {r.status_code} {r.text[:300]}", file=sys.stderr)
    if r.status_code == 200:
        challenge = r.json().get("challenge")
        print(f"  challenge: {challenge[:80]}...", file=sys.stderr)
        print("\n--- 5) attempt v1 register with challenge + signed challenge_response ---", file=sys.stderr)
        try:
            identity = _load_agent_identity()
            print(f"  uagents identity address: {identity.address}", file=sys.stderr)
            print(f"  uagents identity pub_key : {identity.pub_key}", file=sys.stderr)
            print(f"  challenge (first 80): {challenge[:80]}", file=sys.stderr)
            sig = _sign_challenge_b64(identity, challenge)
            print(f"  challenge_response (base64): {sig}", file=sys.stderr)
            body = dict(payload)
            body["challenge"] = challenge
            body["challenge_response"] = sig
            r = client.post(
                "https://agentverse.ai/v1/agents",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=body,
                timeout=30,
                follow_redirects=False,
            )
            print(f"  HTTP {r.status_code} {r.text[:400]}", file=sys.stderr)
            if r.status_code in (200, 201):
                print("  AGENTVERSE REGISTRATION ACCEPTED", file=sys.stderr)
            else:
                print("  (v1 endpoint returned non-2xx; skipping further v1 attempts in diagnose)", file=sys.stderr)
        except SystemExit as exc:
            print(f"  skipped signing: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
