"""Agentverse registration helper for the EventPilot coordinator.

This is a one-shot helper meant to run on a **trusted host that possesses the
coordinator agent's private key**. The Agentverse v1 API no longer accepts a
plain API-key POST to /v2/agents; it now uses a signed challenge/response flow:

  1. POST https://agentverse.ai/v1/auth/challenge  with {"address": <addr>}
     using the AGENTVERSE_API_KEY bearer token.
  2. POST https://agentverse.ai/v1/agents with the agent payload, the challenge
     JWT, and a signature over the challenge produced with the **agent's private
     key** (the same seed the coordinator is started from).

A register_only mode is also provided for the (currently broken on the live API)
v2 endpoint so the script can be used for debugging / future compatibility.

Required environment variables (at minimum for register mode):
  AGENTVERSE_API_KEY
  AGENTVERSE_API_URL (optional; defaults to https://agentverse.ai/v2)
  AGENT_ADDRESS / COORDINATOR_ADDRESS  (defaults to the deployed coordinator address)
  AGENT_ENDPOINT  (defaults to the deployed /submit URL)

For register mode you additionally need one of:
  AGENT_SEED               (hex seed string, same as COORDINATOR_SEED)
  or AGENT_PRIVATE_KEY_PEM (path to an ECDSA private key PEM; if AGENT_SEED is
                             set the script will generate the matching key)
  or AGENT_PRIVATE_KEY_UARTS (base58/hex of a uagents-format private key; the
                             script derives the EC key from it and signs with it)

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
from pathlib import Path
from typing import Any

import httpx

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


def _base58_decode_to_bytes(s58: str) -> bytes:
    # Minimal base58 (Bitcoin alphabet) decoder sufficient for uagents-style
    # private key words. Import a real library if one is available.
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    num = 0
    for ch in s58:
        num = num * len(alphabet) + alphabet.index(ch)
    out = bytearray()
    while num > 0:
        out.append(num % 256)
        num //= 256
    return bytes(out[::-1])


def _hex_to_bytes(h: str) -> bytes:
    h = h.strip().replace("0x", "").replace("_", "").lower()
    return bytes.fromhex(h)


def _load_agent_private_key() -> Any:
    """Return an EC private key (cryptography.hazmat) for signing the challenge.

    Prefers AGENT_PRIVATE_KEY_PEM, then AGENT_SEED (generating an EC key from the
    seed deterministically), then AGENT_PRIVATE_KEY_UARTS.
    """
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend

    pem_path = _env("AGENT_PRIVATE_KEY_PEM")
    if pem_path:
        data = Path(pem_path).read_bytes()
        for loader in (
            lambda d: serialization.load_pem_private_key(d, password=None, backend=default_backend()),
            lambda d: serialization.load_der_private_key(d, password=None, backend=default_backend()),
        ):
            try:
                return loader(data)
            except ValueError:
                continue
        raise SystemExit(f"Could not parse private key PEM/DER from {pem_path}")

    seed = _env("AGENT_SEED")
    if seed:
        # Derive an ECDSA P-256 private key deterministically from the seed.
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from cryptography.hazmat.primitives import hashes

        seed_bytes = _hex_to_bytes(seed)
        # uagents seeds are typically 32 bytes (64 hex). If it's longer/shorter
        # we still derive a 32-byte key via HKDF so we always produce a valid key.
        derived = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"eventpilot-agentverse-registration",
            backend=default_backend(),
        ).derive(seed_bytes)
        return ec.derive_private_key(int.from_bytes(derived, "big"), ec.SECP256R1(), default_backend())

    uart = _env("AGENT_PRIVATE_KEY_UARTS")
    if uart:
        raw = _base58_decode_to_bytes(uart)
        # uagents private key words are 32 bytes on mainnet/testnet for secp256k1.
        # Try the last 32 bytes if the word is longer, otherwise the whole word.
        candidate = raw[-32:] if len(raw) >= 32 else raw
        if len(candidate) != 32:
            raise SystemExit("AGENT_PRIVATE_KEY_UARTS did not decode to >=32 bytes")
        return ec.derive_private_key(int.from_bytes(candidate, "big"), ec.SECP256R1(), default_backend())

    raise SystemExit(
        "No agent private key available for the signed challenge.\n"
        "Set one of: AGENT_SEED, AGENT_PRIVATE_KEY_PEM, AGENT_PRIVATE_KEY_UARTS.\n"
        "If you do not have the agent private key, use the mailbox flow "
        "(AGENT_MAILBOX=true + connect mailbox from the Agentverse inspector) "
        "or the public-endpoint flow instead of this script."
    )


def _jwt_sign_ecdsa(data: str, private_key: Any) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, utils
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization
    raw = private_key.sign(data.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    if isinstance(raw, bytes) and raw[:2] == b"\x30":
        r, s = utils.decode_dss_signature(raw)
        sig = utils.encode_dss_signature(r, s)
    else:
        sig = raw
    return base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii")


def _jwt_sign_ecdsa_raw(data: str, private_key: Any) -> bytes:
    """Return the raw (p1363) 64-byte signature, no base64."""
    from cryptography.hazmat.primitives.asymmetric import ec, utils
    from cryptography.hazmat.primitives import hashes
    raw = private_key.sign(data.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    if isinstance(raw, bytes) and raw[:2] == b"\x30":
        r, s = utils.decode_dss_signature(raw)
        return utils.encode_dss_signature(r, s)
    return raw


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


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
    for i in range(attempts):
        r = client.post(
            f"{base_url}/v1/auth/challenge",
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
    private_key: Any,
    attempts: int,
) -> httpx.Response:
    # Build the message the server expects a signature over. The challenge JWT
    # carries a server-chosen payload (etpy). We include the canonical JWT
    # fields the server emitted (sorted, no whitespace) since many challenge
    # systems expect a signature of the exact JWT payload bytes. We sign the
    # PEM-formatted public point (uncompressed, leading 0x04) following the
    # uagents/xfp convention so the verifier can reconstruct the agent key.
    header = _decode_jwt_part(challenge, 0)
    payload_jwt = _decode_jwt_part(challenge, 1)
    from cryptography.hazmat.primitives import serialization
    pub_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    addr = payload_jwt.get("sub", "")
    # Variant A: xfp-style (alg:typ:address) with pubkey+challenge appended
    signable_a = (
        header.get("alg", "") + ":" +
        header.get("typ", "") + ":" +
        addr + "\n" +
        pub_bytes.hex() + "\n" +
        challenge
    )
    # Variant B: canonical full JWT payload JSON sorted + pubkey + challenge
    signable_b = (
        json.dumps(payload_jwt, separators=(",", ":"), sort_keys=True) + "\n" +
        pub_bytes.hex() + "\n" +
        challenge
    )
    # Variant C: raw challenge bytes only
    signable_c = challenge
    # Variant D: challenge + pubkey + address
    signable_d = challenge + "\n" + pub_bytes.hex() + "\n" + addr
    # Variant E: xfp-only (alg:typ:address), no challenge, no pubkey
    signable_e = header.get("alg", "") + ":" + header.get("typ", "") + ":" + addr
    print(
        f"[register_v1] signable_a: {signable_a[:200]}",
        f"signable_b: {signable_b[:200]}",
        f"signable_c: {signable_c[:120]}",
        f"signable_d: {signable_d[:200]}",
        f"signable_e(xfp-only): {signable_e[:120]}",
        sep="\n",
        file=sys.stderr,
    )
    sig_a = base64.urlsafe_b64encode(_jwt_sign_ecdsa_raw(signable_a, private_key)).rstrip(b"=").decode()
    sig_b = base64.urlsafe_b64encode(_jwt_sign_ecdsa_raw(signable_b, private_key)).rstrip(b"=").decode()
    sig_c = base64.urlsafe_b64encode(_jwt_sign_ecdsa_raw(signable_c, private_key)).rstrip(b"=").decode()
    sig_d = base64.urlsafe_b64encode(_jwt_sign_ecdsa_raw(signable_d, private_key)).rstrip(b"=").decode()
    sig_e = base64.urlsafe_b64encode(_jwt_sign_ecdsa_raw(signable_e, private_key)).rstrip(b"=").decode()

    body: dict[str, Any] = dict(payload)
    body["challenge"] = challenge
    body["challenge_response"] = sig_a

    variants = [
        ("sig_a(jwt-header-payload-pubchal)", sig_a),
        ("sig_b(canonical-payload-pubchal)", sig_b),
        ("sig_c(raw-challenge)", sig_c),
        ("sig_d(challenge-pub-addr)", sig_d),
        ("sig_e(xfp-only)", sig_e),
    ]
    last = None
    for label, sig_val in variants:
        body["challenge_response"] = sig_val
        for i in range(attempts):
            r = client.post(
                f"{base_url}/v1/agents",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=body,
                timeout=30,
            )
            print(f"  [{label}] attempt {i} -> {r.status_code} {r.text[:300]}", file=sys.stderr)
            if r.status_code in (200, 201):
                print("  AGENTVERSE REGISTRATION ACCEPTED", file=sys.stderr)
                return r
            last = r
            time.sleep(1)
    return last or r


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
                print("\n=== register_only: retry v1 POST against /v1/agents (full payload, challenge signed) ===", file=sys.stderr)
                challenge = fetch_challenge(client, base_url, token, address, challenge_attempts)
                print(f"  challenge obtained: {challenge[:50]}...", file=sys.stderr)
                r4 = register_v1(client, base_url, token, payload, challenge, _load_agent_private_key(), register_attempts)
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
        if not _env("AGENT_SEED") and not _env("AGENT_PRIVATE_KEY_PEM") and not _env("AGENT_PRIVATE_KEY_UARTS"):
            print(
                "AGENTVERSE_REGISTER_MODE=register requires the agent private key.\n"
                "Set AGENT_SEED (matching COORDINATOR_SEED) or AGENT_PRIVATE_KEY_PEM or "
                "AGENT_PRIVATE_KEY_UARTS.",
                file=sys.stderr,
            )
            return 2
        private_key = _load_agent_private_key()
        challenge = fetch_challenge(client, base_url, token, address, challenge_attempts)
        print(f"  challenge obtained: {challenge[:50]}...", file=sys.stderr)
        r = register_v1(client, base_url, token, payload, challenge, private_key, register_attempts)
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
            private_key = _load_agent_private_key()
            from cryptography.hazmat.primitives import serialization as _serialization
            header = _decode_jwt_part(challenge, 0)
            payload_jwt = _decode_jwt_part(challenge, 1)
            pub_bytes = private_key.public_key().public_bytes(
                _serialization.Encoding.X962, _serialization.PublicFormat.UncompressedPoint
            )
            addr = payload_jwt.get("sub", "")
            signable = (
                header.get("alg", "") + ":" +
                header.get("typ", "") + ":" +
                addr + "\n" +
                pub_bytes.hex() + "\n" +
                challenge
            )
            sig = base64.urlsafe_b64encode(_jwt_sign_ecdsa_raw(signable, private_key)).rstrip(b"=").decode()
            print(f"  signing key info: {private_key.__class__.__name__}", file=sys.stderr)
            print(f"  signable payload: {signable[:200]}", file=sys.stderr)
            body = dict(payload)
            body["challenge"] = challenge
            body["challenge_response"] = sig
            r = client.post(
                "https://agentverse.ai/v1/agents",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=body,
                timeout=30,
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
