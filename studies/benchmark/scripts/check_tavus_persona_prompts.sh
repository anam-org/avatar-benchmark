#!/usr/bin/env bash
# Verify each pre-created Tavus persona carries exactly the prompt the mint
# route would have composed for its round.
#
# Verify each pre-created Tavus persona carries exactly the prompt the mint
# route would have composed for its round. Tavus is the one provider whose
# task prompt is not sent at mint.
set -euo pipefail
cd "$(dirname "$0")/.."          # studies/benchmark
repo=$(cd ../.. && pwd)

python3 - "$repo" << 'EOF'
import json, pathlib, subprocess, sys

repo = pathlib.Path(sys.argv[1])
here = pathlib.Path.cwd()
# One personas.json per Tavus arm, each under provider_configs/<arm>/.
records = []
for f in sorted((here / "provider_configs").glob("*/personas.json")):
    for r in json.loads(f.read_text()):
        records.append({**r, "arm": r.get("arm", f.parent.name)})
if not records:
    sys.exit("no provider_configs/*/personas.json found")

rounds = [{"key": f"{r['slot']}:{r['mode']}", "topic": r["topic"], "name": r["name"],
           "training": r["mode"] == "practice"} for r in records]
script = """
import { buildPrompt } from "%s/src/prompts/index.ts";
const rounds = %s;
const out = {};
for (const r of rounds) out[r.key] = buildPrompt({ topic: r.topic, name: r.name, training: r.training });
process.stdout.write(JSON.stringify(out));
""" % (repo, json.dumps(rounds))
res = subprocess.run(["npx", "--yes", "tsx", "-e", script], capture_output=True, text=True, cwd=repo)
if res.returncode != 0:
    sys.exit(f"prompt render failed:\n{res.stderr[-2000:]}")
expected = json.loads(res.stdout)

bad = []
for r in records:
    stored = (here / "provider_configs" / r["arm"]
              / f"{r['slot']}_{r['mode']}_persona_{r['personaId']}_system_prompt.txt")
    if not stored.is_file():
        bad.append(f"{r['arm']} {r['slot']}/{r['mode']}: no stored prompt at {stored.name}")
        continue
    if stored.read_text() != expected[f"{r['slot']}:{r['mode']}"]:
        bad.append(f"{r['arm']} {r['slot']}/{r['mode']}: stored prompt differs from src/prompts")

if bad:
    sys.exit("TAVUS PROMPT PARITY FAILED:\n  " + "\n  ".join(bad))
arms = sorted({r["arm"] for r in records})
print(f"TAVUS PROMPT PARITY OK: {len(records)} personas across {len(arms)} arm(s): {', '.join(arms)}")
EOF
