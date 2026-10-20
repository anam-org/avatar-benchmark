"""Stimulus capability tokens.

A stimulus URL is signed so only a dataset-built page can mint a session,
and the signature covers the parameters that decide what the rater sees.
Changing any of them invalidates the token, so a URL cannot be replayed
against a different avatar, topic, voice or model.

The payload is built in two stages:

    core_payload(...)   the six fields every study binds, in fixed order
    bind(core, *extra)  study-specific fields, appended after the core

A study reaches the payload only through `bind`, which can add segments
and nothing else. Dropping a core field, reordering the core, or making a
core field optional is unreachable from study code — it takes an edit to
`core_payload` itself, which is mirrored in TypeScript and asserted
identical by tools/check_token_parity.sh.

The worker mirror is src/lib/token.ts; the verifier is
src/lib/api.ts verifyUrlToken.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time

# The order is the contract. Both ends join on ":" and neither may vary it.
CORE_FIELDS = ("provider", "id", "topic", "mode", "voice", "model")

DEFAULT_TTL_DAYS = 30


def ttl_days() -> int:
    return int(os.environ.get("BENCHMARK_URL_TTL_DAYS") or DEFAULT_TTL_DAYS)


def url_secret() -> str:
    return os.environ.get("BENCHMARK_URL_SECRET", "")


def core_payload(
    *,
    provider: str,
    id_: str,
    topic: str,
    mode: str,
    voice: str = "",
    model: str = "",
) -> str:
    """The six core segments, keyword-only."""
    return ":".join([provider, id_, topic, mode, voice, model])


def bind(core: str, *extra: str) -> str:
    """Append study-specific segments to a core payload.

    Empty segments are dropped rather than bound as empty strings, so a
    study that binds nothing produces exactly the core payload and URLs
    signed without extras stay valid.
    """
    return ":".join([core, *[e for e in extra if e]])


def sign(payload: str, *, ttl: int | None = None) -> str:
    """Token is '<expiry>.<mac>'. The expiry is unix seconds and is signed
    with the payload. A negative ttl signs an already-expired token."""
    secret = url_secret()
    if not secret:
        raise SystemExit("BENCHMARK_URL_SECRET not set — cannot sign stimulus URLs")
    days = ttl_days() if ttl is None else ttl
    exp = int(time.time()) + days * 86400
    mac = hmac.new(
        secret.encode(), f"{payload}:{exp}".encode(), hashlib.sha256
    ).hexdigest()[:32]
    return f"{exp}.{mac}"
