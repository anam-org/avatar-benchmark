#!/usr/bin/env bash
# The root wrangler.jsonc decides which worker a build deploys to, because
# the Astro Cloudflare adapter bakes it into dist/server/wrangler.json. It
# is a copy of one study's config, selected by the deploy workflow.
#
# Fails if the root config is a copy of no study.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 - "$@" <<'EOF'
import json, re, sys
from pathlib import Path


def load(p: Path):
    """Parse jsonc: strip // comments and trailing commas."""
    t = re.sub(r"^\s*//.*$", "", p.read_text(), flags=re.M)
    t = re.sub(r",(\s*[}\]])", r"\1", t)
    return json.loads(t)


root = load(Path("wrangler.jsonc"))
studies = {p.parent.name: load(p) for p in sorted(Path("studies").glob("*/wrangler.jsonc"))}
if not studies:
    sys.exit("FATAL: no studies/*/wrangler.jsonc found")

match = [name for name, cfg in studies.items() if cfg == root]
if match:
    print(f"WORKER CONFIG OK: root wrangler.jsonc is the {match[0]} study's "
          f"({root['name']}, KV {root['kv_namespaces'][0]['id'][:8]}…)")
    sys.exit(0)

print("ROOT WORKER CONFIG MATCHES NO STUDY")
print(f"  root: name={root.get('name')!r} "
      f"kv={root.get('kv_namespaces', [{}])[0].get('id')!r}")
for name, cfg in studies.items():
    print(f"  studies/{name}: name={cfg.get('name')!r} "
          f"kv={cfg.get('kv_namespaces', [{}])[0].get('id')!r}")
print("\nCopy the study you mean to deploy:")
print("  cp studies/<study>/wrangler.jsonc wrangler.jsonc")
sys.exit(1)
EOF
