#!/usr/bin/env bash
# Re-syncs the vendored config from the repo root first.
set -euo pipefail
cd "$(dirname "$0")"
cp ../config/avatars.json ../config/providers.json ../config/livekit.json ../config/livekit_tavus.json config/
python3 -c "
import json
cfg = json.load(open('config/avatars.json'))
assert 'anam' in cfg['providers'], 'vendored avatars.json missing anam key'
print('vendored config verified')
"
envval() {
  # env wins; otherwise first match in agent/.env, then the study's, then
  # the repo root beside .env.example
  eval "v=\${$1:-}"
  [ -n "$v" ] || v=$(grep -h "^$1=" .env ../.env ../../../.env 2>/dev/null | head -1 | cut -d= -f2-)
  printf '%s' "$v"
}

PROJECT=$(envval LIVEKIT_AGENT_PROJECT)
AGENT_ID=$(envval LIVEKIT_AGENT_ID)
LK_URL=$(envval LIVEKIT_URL)
# The LiveKit Cloud subdomain is the host label of LIVEKIT_URL.
SUBDOMAIN=$(printf '%s' "$LK_URL" | sed -E 's#^[a-z]+://##; s#\..*##')

for pair in "LIVEKIT_AGENT_PROJECT:$PROJECT" "LIVEKIT_URL:$LK_URL" "LIVEKIT_AGENT_ID:$AGENT_ID"; do
  name=${pair%%:*}; val=${pair#*:}
  if [ -z "$val" ]; then
    echo "FATAL: $name not set (environment, agent/.env or .env)" >&2
    exit 1
  fi
done

# livekit.toml names the LiveKit Cloud project and the agent being updated.
cat > livekit.toml <<TOML
[project]
  subdomain = "$SUBDOMAIN"

[agent]
  id = "$AGENT_ID"
TOML
echo "livekit.toml written for subdomain $SUBDOMAIN, agent $AGENT_ID"

exec lk agent deploy --project "$PROJECT" "$@"
