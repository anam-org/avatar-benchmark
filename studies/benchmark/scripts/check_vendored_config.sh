#!/usr/bin/env bash
# Two consumers keep a vendored copy of this study's config beside them:
# the LiveKit agent, which runs from its own directory and prefers
# agent/config when it exists, and the harness worker, which imports JSON
# at build time from src/config and cannot reach across the repo into a
# study. Compare both against the study's config, which is authoritative.
set -euo pipefail
cd "$(dirname "$0")/.."          # studies/benchmark
repo=$(cd ../.. && pwd)

status=0

for f in avatars providers livekit livekit_tavus; do
  if ! diff -q "config/$f.json" "agent/config/$f.json" > /dev/null; then
    echo "DIFFERS: config/$f.json vs agent/config/$f.json"
    diff "config/$f.json" "agent/config/$f.json" || true
    status=1
  fi
done

for f in avatars providers livekit experiment; do
  if ! diff -q "config/$f.json" "$repo/src/config/$f.json" > /dev/null; then
    echo "DIFFERS: config/$f.json vs src/config/$f.json"
    diff "config/$f.json" "$repo/src/config/$f.json" || true
    status=1
  fi
done

if [ "$status" -eq 0 ]; then
  echo "VENDORED CONFIG OK: agent/config and src/config both match config"
fi
exit "$status"
