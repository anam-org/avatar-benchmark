#!/usr/bin/env python3
"""Post-deploy smoke: the variant's session-mint routes, signed and unsigned.

Every route is checked three ways: no token, an expired token, a live one.
The signed check mints one billable session per route. Reads
BENCHMARK_BASE_URL and BENCHMARK_URL_SECRET from the environment or .env;
--base overrides the former.

    python scripts/smoke_worker.py
    python scripts/smoke_worker.py --variant livekit
    python scripts/smoke_worker.py --base https://<deployment>
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import _env

UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh) Chrome/152.0",
    "Content-Type": "application/json",
}

SLOT = "f2"


def post(base: str, path: str, body: dict) -> tuple[int, object]:
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode(), headers=UA, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            return res.status, json.load(res)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:120]
    except urllib.error.URLError as e:
        return 0, f"unreachable: {e.reason}"


def checks_for(variant: str, session_uuid: str) -> list[tuple[str, str, dict, int]]:
    """Three checks per route: unsigned, expired, signed.

    Ids, topics and names come from config/; payloads are built and signed
    with avatar_dataset's own functions.
    """
    # Imported here: avatar_dataset reads BENCHMARK_URL_SECRET at import.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import avatar_dataset as ad

    topic = ad.SLOT_TOPICS[SLOT]
    name = ad.AVATAR_NAMES[SLOT]
    out = []

    def three(label: str, path: str, payload: str, body: dict,
              body_topic: str | None = None) -> None:
        base = {**body, "topic": body_topic or topic, "sessionUuid": session_uuid}
        out.append((f"{label} unsigned 403", path, base, 403))
        out.append((f"{label} expired 403", path,
                    {**base, "k": ad.sign_token(payload, ttl_days=-1)}, 403))
        out.append((f"{label} signed", path,
                    {**base, "k": ad.sign_token(payload)}, 200))

    if variant == "livekit":
        # Every arm the variant can field. The LiveKit mint issues a room
        # token; no avatar session starts until a participant joins.
        pending = [p for p in sorted(ad.PROVIDERS_LIVEKIT) if not ad.avatars_for(p)]
        if pending:
            # Declared in config/livekit.json with no cast; skipped.
            print(f"  (skipping, no cast yet: {', '.join(pending)})")
        for provider in sorted(ad.PROVIDERS_LIVEKIT):
            if not ad.avatars_for(provider):
                continue
            three(
                f"livekit {provider}",
                "/api/livekit",
                ":".join(["livekit", provider, SLOT, topic, "main"]),
                {"provider": provider, "slot": SLOT, "name": name},
            )
        return out

    av = ad.AVATARS
    anam = av["anam"][SLOT]
    three(
        "anam", "/api/anam",
        ad.token_payload("anam", anam["avatarId"], {"topic": topic},
                         voice=anam["voiceId"], model=ad.ANAM_MODEL),
        {"avatarId": anam["avatarId"], "voiceId": anam["voiceId"],
         "model": ad.ANAM_MODEL},
    )
    heygen = av["heygen"][SLOT]
    three(
        "heygen", "/api/heygen",
        ad.token_payload("heygen", heygen["avatarId"], {"topic": topic},
                         voice=heygen["voiceId"]),
        {"avatarId": heygen["avatarId"], "voiceId": heygen["voiceId"], "name": name},
    )
    tavus = av["tavus"][SLOT]
    three(
        "tavus", "/api/tavus",
        ad.token_payload("tavus", tavus["replicaId"], {"topic": topic}),
        {"replicaId": tavus["replicaId"], "name": name},
    )
    # LemonSlice bakes the topic into the agent, so the payload carries the
    # agent's fixedTopic rather than the slot's topic (avatar_dataset
    # .build_lemonslice does the same).
    lemonslice = av["lemonslice"][SLOT]
    ls_topic = lemonslice["fixedTopic"]
    three(
        "lemonslice", "/api/lemonslice",
        ad.token_payload("lemonslice", lemonslice["avatarId"], {"topic": ls_topic}),
        {"avatarId": lemonslice["avatarId"], "name": name},
        ls_topic,
    )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", choices=("turnkey", "livekit"), default="turnkey")
    ap.add_argument("--base", help="target origin (default: BENCHMARK_BASE_URL)")
    args = ap.parse_args()

    base = (args.base or _env.require("BENCHMARK_BASE_URL")).rstrip("/")
    _env.require("BENCHMARK_URL_SECRET")

    checks = checks_for(args.variant, str(uuid.uuid4()))
    print(f"smoke: {args.variant} routes against {base}")

    fails = 0
    conversations = []
    for label, path, body, want in checks:
        code, resp = post(base, path, body)
        ok = code == want
        fails += 0 if ok else 1
        detail = "" if ok else f"  <- {str(resp)[:100]}"
        print(f"{'OK ' if ok else 'FAIL'} {label}: {code} (want {want}){detail}")
        if path == "/api/tavus" and isinstance(resp, dict) and resp.get("conversationId"):
            conversations.append(resp["conversationId"])

    for cid in conversations:
        req = urllib.request.Request(
            base + "/api/tavus",
            data=json.dumps({"conversationId": cid}).encode(),
            headers=UA, method="DELETE",
        )
        try:
            urllib.request.urlopen(req, timeout=30)
            print(f"   cleaned up tavus conversation {cid[:10]}")
        except urllib.error.URLError as e:
            print(f"   WARN could not end tavus conversation {cid[:10]}: {e.reason}")
    return fails


if __name__ == "__main__":
    raise SystemExit(main())
