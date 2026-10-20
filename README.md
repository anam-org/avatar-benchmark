# Anam human evaluation

Anam's human-rater study of real-time interactive avatar providers, run on
the Mabyduck platform.

## Quickstart: reproduce the results

Use Python 3.10+ to analyse the included evidence bundles offline. These
commands require no API keys, accounts or additional Python packages:

```sh
cd studies/benchmark
python3 scripts/4_analyse.py --variant turnkey
python3 scripts/4_analyse.py --variant livekit
```

To also recompute reply latencies from the included audio envelopes, add
`--check-envelopes` to either command.

The bundles contain two studies: each provider's own full stack, and a
shared LiveKit pipeline. See the [study guide](studies/benchmark/README.md#reproducing-the-published-results)
for the bundle contents and calculation details. Its *Running a study*
section covers recruiting new raters using your own provider accounts.

If you need help reproducing anything, contact support@anam.ai.

## Layout

```
studies/benchmark/   the study: harness config, launchers, analysis, evidence
packages/            code the study imports
tools/               repo-level checks
src/, public/        the harness worker (Astro on Cloudflare)
```

Start at `studies/benchmark/README.md`: how to run the study, and how to
reproduce the published results from the bundles in
`studies/benchmark/evidence/`.

## Shared packages

- `packages/stimulus` — capability tokens, the iframe wrapper, dataset zip
- `packages/mabyduck` — Mabyduck API client: datasets, experiments, jobs

```
pip install -e packages/stimulus -e packages/mabyduck
```

## The token contract

Stimulus URLs are signed, and the signature covers the parameters that
decide what a rater sees, so a URL cannot be replayed against a different
avatar, topic, voice or model.

`stimulus.core_payload` binds those six fields in a fixed order;
`stimulus.bind` appends study-specific segments after them. A study can
add a bound parameter and nothing else — dropping or reordering a core
field takes an edit to `core_payload` itself. `src/lib/token.ts` is the
worker's mirror, and `tools/check_token_parity.sh` fails if the two drift.

## The worker

One Astro app serves the stimulus pages, deployed to a Cloudflare Worker
with its own KV namespace and signing secret, defined by
`studies/benchmark/wrangler.jsonc`.

Before deploying, create your own Cloudflare KV namespace and replace
`REPLACE_WITH_YOUR_KV_NAMESPACE_ID` in that study configuration with its ID.
See the [hosting instructions](studies/benchmark/README.md#one-way-cloudflare-workers-and-kv).

The deploy target is decided at **build** time, not deploy time: the Astro
Cloudflare adapter bakes the root `wrangler.jsonc` into
`dist/server/wrangler.json`, so `wrangler deploy -c <study config>` would
upload the assets without the worker. Select the study first:

```
cp studies/benchmark/wrangler.jsonc wrangler.jsonc
npm run build && npx wrangler deploy
```

The deploy workflow takes the study as an input and does exactly that.
`tools/check_worker_config.sh` fails if the root config is a copy of no
study. Deploys are `workflow_dispatch` only, so a push never redeploys
mid-study.
