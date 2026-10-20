#!/usr/bin/env python3
"""Show what a local dry-run session actually did, from the local KV store.

`wrangler dev` writes evidence to a local KV namespace under .wrangler/state
rather than the study's. This reads it back and prints, per session, the
milestone sequence the harness recorded:

  * did the kickoff fire on the provider's own readiness signal, or on the
    fallback timer? (`kickoff-sent` detail)
  * did the session carry a presentation id through to the manifest?
  * could this machine's network do WebRTC at all? (`ice`)
  * where exactly did a failed session stop?

    npm run build && npx wrangler dev --port 8788     # in another terminal
    python scripts/local_stimulus_urls.py --base http://localhost:8788
    ...run a session in the browser...
    python scripts/inspect_local_sessions.py

Reads the local store only.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from fire import Fire

ROOT = Path(__file__).resolve().parent.parent
REPO = Path(__file__).resolve().parents[3]  # repo root: worker, wrangler, node


def _wrangler(*args: str) -> str:
    out = subprocess.run(
        ["npx", "wrangler", *args, "--binding", "MANIFEST", "--local"],
        cwd=REPO, capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise SystemExit(
            f"wrangler failed: {out.stderr.strip()[-400:]}\n"
            "Has `npx wrangler dev` run at least once?"
        )
    return out.stdout


def _keys() -> list[str]:
    raw = _wrangler("kv", "key", "list")
    start = raw.find("[")
    if start < 0:
        return []
    return [k["name"] for k in json.loads(raw[start:])]


def main(since_minutes: int = 60, limit: int = 20, events: bool = True) -> None:
    """Print recent local sessions, newest last.

    Args:
      since_minutes: how far back to look. Keys carry their write time.
      limit: most recent N sessions to fetch.
      events: print the full milestone sequence, not just the summary.
    """
    cutoff_ms = (time.time() - since_minutes * 60) * 1000
    client = sorted(
        k for k in _keys()
        if k.startswith("client:") and float(k.split(":")[1]) >= cutoff_ms
    )
    if not client:
        raise SystemExit(
            f"no client manifests in the last {since_minutes} min.\n"
            "Run a session first, and let it finish or press End Call — the "
            "manifest is written when the session ends."
        )

    for key in client[-limit:]:
        d = json.loads(_wrangler("kv", "key", "get", key))
        conn = d.get("connectivity") or {}
        log = d.get("events") or []
        kick = next(
            (e for e in log if e.get("event") in ("kickoff-sent", "kickoff-failed")),
            None,
        )
        print(f"\n{'=' * 72}")
        print(f"{d.get('provider') or '?':11} {d.get('sessionUuid') or '?'}")
        print(f"  ended      {d.get('endState')}"
              f"   connect {d.get('connectMs')} ms"
              f"   duration {d.get('durationSeconds')} s")
        print(f"  pid        {d.get('presentationId') or '(none — page opened without one)'}")
        if kick:
            print(f"  kickoff    {kick['event']} via {kick.get('detail')}"
                  f"  at {kick['t']} ms")
        elif (d.get("provider") or "").startswith("livekit-"):
            print("  kickoff    n/a — the shared pipeline opens the conversation")
        elif d.get("provider") == "anam":
            print("  kickoff    n/a — Anam takes the first turn itself")
        else:
            print("  kickoff    NONE SENT  <-- the avatar would wait for the rater")
        if conn:
            types = "+".join(conn.get("candidateTypes") or []) or "none"
            verdict = "srflx" if conn.get("srflx") else "host only"
            # srflxMs is the meaningful latency. gatherMs only measures
            # anything when gathering finished on its own; otherwise it is
            # just the probe's timeout.
            timing = (f"srflx {conn.get('srflxMs')} ms" if conn.get("srflx")
                      else "no srflx")
            if not conn.get("complete"):
                timing += f", gathering did not finish within {conn.get('gatherMs')} ms"
            print(f"  ice        {types}  ({verdict})  {timing}")
        if d.get("errorDetail"):
            print(f"  error      {d['errorDetail']}")
        if events and log:
            print("  milestones")
            for e in log:
                detail = f"  {e['detail']}" if e.get("detail") is not None else ""
                print(f"    {e['t']:>7} ms  {e['event']}{detail}")

    print(f"\n{'=' * 72}")
    print(f"{len(client[-limit:])} session(s). Healthy turnkey runs show a "
          "kickoff via the provider's own signal:")
    print("  tavus  pal-ready   |   heygen  connected   |   lemonslice  bot-ready")
    print("A tavus detail of 'fallback' means neither readiness event arrived "
          "and the timer sent the note; 'retry attempt=N' means an earlier note "
          "was not acknowledged.")


if __name__ == "__main__":
    Fire(main)
