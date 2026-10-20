"""One-time setup: create an echo-mode Tavus persona per study replica.

The Tavus LiveKit plugin drives a replica with OUR pipeline's audio, which
requires a persona with pipeline_mode="echo". Writes the
replica_id -> persona_id map to config/livekit_tavus.json.

Covers the base Tavus arm and every Tavus arm declared in
config/providers.json. Each arm carries its own cast, because Tavus does not
serve one replica on two models, so adding an arm adds a replica set that needs
personas of its own.

A replica already in the map is left alone, so a run creates only what is
missing; --force recreates every persona and replaces the ids the study was
fielded with. The map records ids, not their state on the account: --dry-run
reports what the file holds, not whether those personas still exist.

Usage: TAVUS_API_KEY=... python agent/setup_tavus_personas.py
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "config" / "livekit_tavus.json"


def _replicas() -> dict[str, str]:
    """replica id -> "<arm>/<slot>", over the base arm and every Tavus arm."""
    avatars = json.loads((ROOT / "config" / "avatars.json").read_text())
    providers = json.loads((ROOT / "config" / "providers.json").read_text())
    arms = ["tavus", *providers["tavus"].get("arms", {})]
    out: dict[str, str] = {}
    for arm in arms:
        for slot, ids in avatars["providers"][arm].items():
            out[ids["replicaId"]] = f"{arm}/{slot}"
    return out


def _create(key: str, replica: str, label: str) -> str:
    req = urllib.request.Request(
        "https://tavusapi.com/v2/personas",
        data=json.dumps(
            {
                "persona_name": f"benchmark-echo-{label.replace('/', '-')}",
                "pipeline_mode": "echo",
                "default_replica_id": replica,
                # Required for LiveKit use per
                # docs.livekit.io/agents/integrations/avatar/tavus/
                "layers": {"transport": {"transport_type": "livekit"}},
            }
        ).encode(),
        headers={"x-api-key": key, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["persona_id"]


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true",
                    help=f"recreate every persona, replacing the ids in {OUT.name}")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be created and exit")
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    load_dotenv(here / ".env")
    load_dotenv(ROOT / ".env")
    load_dotenv(here.parents[2] / ".env")  # repo root, beside .env.example

    held: dict[str, str] = json.loads(OUT.read_text()) if OUT.exists() else {}
    replicas = _replicas()
    missing = {r: label for r, label in replicas.items()
               if args.force or r not in held}

    for replica, label in sorted(replicas.items(), key=lambda kv: kv[1]):
        state = "create" if replica in missing else f"held {held[replica]}"
        print(f"  {label:<26} {replica}  {state}")
    stale = sorted(set(held) - set(replicas))
    for replica in stale:
        print(f"  {'(no longer cast)':<26} {replica}  held {held[replica]}")

    if not missing:
        print(f"\nnothing to create; {OUT.relative_to(ROOT)} covers every replica")
        return
    print(f"\n{len(missing)} persona(s) to create")
    if args.dry_run:
        print("dry run, nothing created")
        return

    key = os.environ.get("TAVUS_API_KEY")
    if not key:
        raise SystemExit("FATAL: TAVUS_API_KEY not set (environment or .env)")

    # Entries the run does not touch are carried through, so adding an arm
    # leaves the ids already in the map unchanged.
    out = dict(held)
    for replica, label in sorted(missing.items(), key=lambda kv: kv[1]):
        out[replica] = _create(key, replica, label)
        print(f"{label} {replica} -> {out[replica]}")

    OUT.write_text(json.dumps(out, indent=2) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
