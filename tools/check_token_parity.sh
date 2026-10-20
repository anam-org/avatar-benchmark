#!/usr/bin/env bash
# Verify the stimulus token payload is built identically in Python and
# TypeScript: the core payload, the core with the study segment bound, and
# the empty-segment case, where a study binds no segment of its own.
set -euo pipefail
cd "$(dirname "$0")/.."

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

cases='[
  ["anam","av-1","animals","main","voice-1","cara-4",""],
  ["anam","av-1","animals","main","voice-1","cara-4","model-a"],
  ["anam","av-1","clothing","practice","voice-1","cara-4","model-b"],
  ["tavus","r-9","sports","practice","","",""],
  ["heygen","h-3","food","main","v-2","",""]
]'

npx --yes tsx -e '
import { corePayload, bindPayload, CORE_FIELDS } from "./src/lib/token.ts";
process.stdout.write(CORE_FIELDS.join(",") + "\n");
for (const [provider, id, topic, mode, voice, model, version] of '"$cases"') {
  // Mirrors the runtime path: the adapter binds the version onto the core.
  process.stdout.write(
    bindPayload(corePayload({ provider, id, topic, mode, voice, model }), version) + "\n",
  );
}
' > "$work/ts.txt"

python3 - > "$work/py.txt" << EOF
import sys
from stimulus import CORE_FIELDS, bind, core_payload
sys.stdout.write(",".join(CORE_FIELDS) + "\n")
for provider, id_, topic, mode, voice, model, version in $cases:
    # Mirrors avatar_dataset.token_payload.
    sys.stdout.write(
        bind(
            core_payload(
                provider=provider, id_=id_, topic=topic, mode=mode,
                voice=voice, model=model,
            ),
            version,
        )
        + "\n"
    )
EOF

diff "$work/ts.txt" "$work/py.txt" && echo "TOKEN PARITY OK: core field order and all payloads match across Python and TypeScript"
