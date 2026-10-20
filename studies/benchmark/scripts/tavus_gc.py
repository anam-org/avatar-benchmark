#!/usr/bin/env python3
"""Delete study Tavus personas that are no longer referenced.

A persona is eligible only if ALL of:

  * its system_prompt contains the study task marker;
  * pipeline_mode is "full" — the echo personas are the LiveKit study's,
    created once per account by agent/setup_tavus_personas.py;
  * it is not named in config/livekit_tavus.json;
  * it is older than --older-than-hours;
  * no conversation is currently active against it.

Dry run by default: it prints what it would delete and exits. Pass --delete
to act.

    python scripts/tavus_gc.py                       # what would go
    python scripts/tavus_gc.py --older-than-hours 48
    python scripts/tavus_gc.py --delete

Needs TAVUS_API_KEY.
"""

from __future__ import annotations

import argparse
import http.client
import json
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import _env

ROOT = Path(__file__).resolve().parent.parent
HOST = "https://tavusapi.com"
TASK_MARKER = "20 Questions"


class ApiError(Exception):
    """A request that did not complete. Distinct from one that returned
    nothing: a listing that fails must not read as an empty account."""


def api(key: str, path: str, method: str = "GET") -> dict:
    """One request. Raises ApiError if it did not complete."""
    req = urllib.request.Request(
        f"{HOST}{path}", method=method, headers={"x-api-key": key}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            body = res.read().decode()
    except (OSError, http.client.HTTPException) as e:
        raise ApiError(f"{method} {path}: {e}") from e
    try:
        return json.loads(body) if body.strip() else {}
    except json.JSONDecodeError:
        return {}


def _page(key: str, path: str) -> list[dict]:
    """One page of a listing. A body without "data" is a failed request, not
    an empty account — Cloudflare and Tavus both answer 200 with one."""
    d = api(key, path)
    if not isinstance(d, dict) or "data" not in d:
        raise ApiError(f"{path}: response carries no 'data' (got {type(d).__name__})")
    return d["data"] or []


def all_personas(key: str) -> list[dict]:
    out, page = [], 1
    while True:
        batch = _page(key, f"/v2/personas?limit=100&page={page}")
        out += batch
        if len(batch) < 100:
            return out
        page += 1
        if page > 100:                      # runaway guard, not a real bound
            return out


def active_persona_ids(key: str) -> set[str]:
    """Personas backing a conversation that has not ended."""
    live, page = set(), 1
    while True:
        batch = _page(key, f"/v2/conversations?limit=100&page={page}")
        for c in batch:
            if c.get("status") in ("active", "starting"):
                if c.get("persona_id"):
                    live.add(c["persona_id"])
        if len(batch) < 100:
            return live
        page += 1
        if page > 100:
            return live


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--delete", action="store_true",
                    help="actually delete; otherwise report and exit")
    ap.add_argument("--older-than-hours", type=int, default=24,
                    help="leave anything newer than this alone (default 24)")
    args = ap.parse_args()

    key = _env.require("TAVUS_API_KEY")
    keep_ids = set(json.loads(
        (ROOT / "config" / "livekit_tavus.json").read_text()).values())
    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.older_than_hours)

    personas = all_personas(key)
    print(f"personas in account: {len(personas)}")
    live = active_persona_ids(key)
    print(f"personas backing an active conversation: {len(live)}")

    doomed, kept = [], Counter()
    for p in personas:
        pid = p.get("persona_id")
        if TASK_MARKER not in (p.get("system_prompt") or ""):
            kept["not minted by this harness"] += 1
            continue
        if p.get("pipeline_mode") != "full":
            kept["echo persona (LiveKit study)"] += 1
            continue
        if pid in keep_ids:
            kept["named in config/livekit_tavus.json"] += 1
            continue
        if pid in live:
            kept["conversation still active"] += 1
            continue
        created = (p.get("created_at") or "").replace("Z", "+00:00")
        try:
            when = datetime.fromisoformat(created)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
        except ValueError:
            kept["unparseable created_at"] += 1
            continue
        if when > cutoff:
            kept[f"newer than {args.older_than_hours}h"] += 1
            continue
        doomed.append(p)

    print(f"\neligible for deletion: {len(doomed)}")
    for reason, n in kept.most_common():
        print(f"  kept, {reason}: {n}")
    if doomed:
        by_day = Counter((p.get("created_at") or "")[:10] for p in doomed)
        print("\n  by creation date:")
        for day, n in sorted(by_day.items()):
            print(f"    {day}  {n}")

    if not args.delete:
        print("\nDry run. Pass --delete to remove them.")
        return
    if not doomed:
        return

    print(f"\ndeleting {len(doomed)}...")
    gone = failed = 0
    for i, p in enumerate(doomed, 1):
        try:
            api(key, f"/v2/personas/{p['persona_id']}", method="DELETE")
            gone += 1
        except ApiError:
            failed += 1
        if i % 50 == 0:
            print(f"  {i}/{len(doomed)}  ({gone} deleted, {failed} failed)", flush=True)
    print(f"\ndeleted {gone}, failed {failed}")
    try:
        print(f"personas now in account: {len(all_personas(key))}")
    except ApiError as e:
        print(f"could not re-count personas: {e}")


if __name__ == "__main__":
    try:
        main()
    except ApiError as e:
        raise SystemExit(f"FATAL: Tavus API request failed: {e}")
