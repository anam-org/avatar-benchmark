"""Create the study's Tavus personas once, ahead of the run.

Tavus takes the task as a persona system_prompt rather than at mint. This
creates one persona per fielded (slot, mode) and records the ids, so a session
makes a single /v2/conversations call. The prompt is rendered by the study's
TypeScript builder rather than a Python port.

Each Tavus arm has its own cast — Tavus does not serve one replica on two
models — so personas are created per arm and recorded under
provider_configs/<arm>/.

    python3 create_tavus_personas.py --arm tavus-phoenix-4-5
    python3 create_tavus_personas.py --dry-run  # render prompts, call nothing
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / "config"
PROVIDER_CONFIGS = Path(__file__).resolve().parent.parent / "provider_configs"
REPO = Path(__file__).resolve().parents[3]
TAVUS_HOST = "https://tavusapi.com"


def render_prompts(rounds: list[dict]) -> dict[str, str]:
    """Render each round's system prompt with src/prompts, the same module
    the mint route composes with."""
    script = """
import { buildPrompt } from "%s/src/prompts/index.ts";
const rounds = %s;
const out = {};
for (const r of rounds) out[r.key] = buildPrompt({ topic: r.topic, name: r.name, training: r.training });
process.stdout.write(JSON.stringify(out));
""" % (REPO, json.dumps(rounds))
    res = subprocess.run(
        ["npx", "--yes", "tsx", "-e", script],
        capture_output=True, text=True, cwd=REPO, check=False,
    )
    if res.returncode != 0:
        raise SystemExit(f"prompt render failed:\n{res.stderr[-2000:]}")
    return json.loads(res.stdout)


def fielded_rounds(arm: str) -> list[dict]:
    avatars = json.loads((CONFIG / "avatars.json").read_text())
    names, slot_topics = avatars["names"], avatars["slotTopics"]
    replicas = avatars["providers"][arm]
    rounds = []
    for slot in avatars["mainSlots"]:
        rounds.append({
            "key": f"{slot}:main", "slot": slot, "mode": "main",
            "topic": slot_topics[slot], "name": names[slot],
            "training": False, "replicaId": replicas[slot]["replicaId"],
        })
    training_slot = avatars["trainingSlot"]
    rounds.append({
        "key": f"{training_slot}:practice", "slot": training_slot, "mode": "practice",
        "topic": avatars["practiceTopic"], "name": names[training_slot],
        "training": True, "replicaId": replicas[training_slot]["replicaId"],
    })
    return rounds


def tavus(path: str, key: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"{TAVUS_HOST}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "x-api-key": key},
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Tavus {path} failed: {e.code} {e.read().decode()[:400]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", default="tavus",
                    help="arm key in avatars.json providers (default: tavus)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    out_dir = PROVIDER_CONFIGS / args.arm
    rounds = fielded_rounds(args.arm)
    prompts = render_prompts(rounds)
    print(f"rendered {len(prompts)} prompts from src/prompts")

    if args.dry_run:
        for r in rounds:
            print(f"  {r['key']:<14} replica={r['replicaId']}  prompt={len(prompts[r['key']])} chars")
        return

    key = os.environ.get("TAVUS_API_KEY")
    if not key:
        raise SystemExit("TAVUS_API_KEY not set")

    out_dir.mkdir(parents=True, exist_ok=True)
    # A persona already recorded for this (replica, mode) is kept. A round
    # whose replica has changed is the only one that needs a new one, and
    # re-creating the rest would leave duplicates behind on the account.
    existing = {}
    record_file = out_dir / "personas.json"
    if record_file.is_file():
        existing = {(e["replicaId"], e["mode"]): e for e in json.loads(record_file.read_text())}

    by_replica: dict[str, dict[str, str]] = {}
    records = []
    for r in rounds:
        prompt = prompts[r["key"]]
        persona_name = f"anam-benchmark-{args.arm}-{r['slot']}-{r['mode']}"
        prior = existing.get((r["replicaId"], r["mode"]))
        if prior:
            by_replica.setdefault(r["replicaId"], {})[r["mode"]] = prior["personaId"]
            records.append(prior)
            print(f"  kept    {prior['personaName']}  {prior['personaId']}")
            continue
        created = tavus("/v2/personas", key, {
            "persona_name": persona_name,
            "system_prompt": prompt,
            "default_replica_id": r["replicaId"],
        })
        pid = created["persona_id"]
        by_replica.setdefault(r["replicaId"], {})[r["mode"]] = pid
        records.append({**{k: r[k] for k in ("slot", "mode", "topic", "name", "replicaId")},
                        "arm": args.arm, "personaName": persona_name, "personaId": pid})
        (out_dir / f"{r['slot']}_{r['mode']}_persona_{pid}_system_prompt.txt").write_text(prompt)
        print(f"  created {persona_name}  {pid}")

    (out_dir / "personas.json").write_text(json.dumps(records, indent=2) + "\n")
    print("\nMerge into config/providers.json under \"tavus\".\"personas\" (and re-vendor):")
    print(json.dumps({"personas": by_replica}, indent=2))


if __name__ == "__main__":
    main()
