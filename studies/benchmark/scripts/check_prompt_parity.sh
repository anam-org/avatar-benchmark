#!/usr/bin/env bash
# Verify the agent's Python port of the 20 Questions prompt stays
# byte-identical to the turnkey TS composition (src/prompts/twentyQuestions.ts).
set -euo pipefail
cd "$(dirname "$0")/.."          # studies/benchmark
repo=$(cd ../.. && pwd)          # worker source lives at the repo root

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

npx --yes tsx -e '
import { buildTwentyQuestionsPrompt } from "'"$repo"'/src/prompts/twentyQuestions.ts";
for (const training of [false, true])
  for (const [topic, name] of [["transportation", "Mia"], ["sports", "Robert"]]) {
    process.stdout.write(`=== ${topic}/${name}/${training}\n`);
    process.stdout.write(buildTwentyQuestionsPrompt(topic, name, { training }));
  }
' > "$work/ts.txt"

python3 - > "$work/py.txt" << 'EOF'
import sys
sys.path.insert(0, "agent/src")
from prompt import build_twenty_questions_prompt
for training in (False, True):
    for topic, name in (("transportation", "Mia"), ("sports", "Robert")):
        sys.stdout.write(f"=== {topic}/{name}/{str(training).lower()}\n")
        sys.stdout.write(build_twenty_questions_prompt(topic, name, training))
EOF

diff "$work/ts.txt" "$work/py.txt" && echo "PARITY OK: Python port is byte-identical to twentyQuestions.ts for the four renderings compared"
