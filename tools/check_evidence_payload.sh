#!/usr/bin/env bash
# Verify that the payload the session page derives from its own URL is the
# payload that URL's signature actually covers.
#
# The page holds a signed URL but not the payload the signature is over, so
# /api/manifest and /api/recording take the token AND a reconstructed
# payload string and check one against the other. When the two derivations
# disagree the session still runs normally and every write it makes is
# rejected 403 — no delivered-video samples, no dropped-frame counts, no
# recording — which is invisible from the rating platform's side.
#
# Every stimulus URL the dataset builder emits is signed here and verified
# against src/lib/stimulusAuth.ts.
set -euo pipefail
cd "$(dirname "$0")/.."

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# A throwaway secret: this checks that two derivations agree, not that any
# real token is valid, so it must not depend on the deployment's secret.
export BENCHMARK_URL_SECRET="check-evidence-payload"
export BENCHMARK_BASE_URL="https://example.invalid"

python3 - "$work/urls.txt" << 'EOF'
"""Emit every stimulus URL the dataset builder produces."""
import re
import sys
from pathlib import Path

sys.path[:0] = ["studies/benchmark/scripts"]

import avatar_dataset

out = Path(sys.argv[1])
work = out.parent
urls: list[str] = []


def collect(root: Path) -> None:
    for js in sorted(root.rglob("*.js")):
        urls.extend(re.findall(r'https://example\.invalid/[^"\']+', js.read_text()))


for training in (False, True):
    d = work / f"benchmark_{training}"
    avatar_dataset.build_folder(
        out_root=d,
        provider_list=list(avatar_dataset.PROVIDERS),
        training=training,
    )
    collect(d)

if not urls:
    raise SystemExit("no stimulus URLs built — the builders emitted nothing")
out.write_text("\n".join(urls) + "\n")
print(f"{len(urls)} stimulus URL(s)")
EOF

# What the page will send back, derived from the URL alone.
npx --yes tsx -e '
import { readFileSync } from "node:fs";
import { stimulusPayload } from "./src/lib/stimulusAuth.ts";
const urls = readFileSync(process.argv[1], "utf8").trim().split("\n");
for (const raw of urls) {
  const u = new URL(raw);
  process.stdout.write(stimulusPayload(u.pathname, u.searchParams) + "\n");
}
' "$work/urls.txt" > "$work/derived.txt"

python3 - "$work/urls.txt" "$work/derived.txt" << 'EOF'
"""Re-sign each derived payload and compare with the URL's own token."""
import hashlib
import hmac
import sys
import urllib.parse

secret = b"check-evidence-payload"
urls = open(sys.argv[1]).read().split()
derived = open(sys.argv[2]).read().split("\n")[: len(urls)]

bad = []
for url, payload in zip(urls, derived):
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    token = q["k"][0]
    exp, _, presented = token.partition(".")
    mac = hmac.new(
        secret, f"{payload}:{int(exp)}".encode(), hashlib.sha256
    ).hexdigest()[:32]
    if mac != presented:
        bad.append((url, payload))

for url, payload in bad:
    print(f"MISMATCH\n  url:     {url}\n  derived: {payload}", file=sys.stderr)
if bad:
    raise SystemExit(
        f"{len(bad)}/{len(urls)} stimulus URL(s) derive a payload their own "
        "signature does not cover — evidence writes from these pages would "
        "be rejected"
    )
print(
    f"EVIDENCE PAYLOAD OK: all {len(urls)} stimulus URLs derive the payload "
    "their signature covers"
)
EOF
