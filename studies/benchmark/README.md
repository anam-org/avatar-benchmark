# Anam avatar benchmark

Study harness for a comparative evaluation of real-time interactive avatar
providers (Anam, Tavus, HeyGen LiveAvatar, LemonSlice, and Synthesia in the
LiveKit study). Raters hold a live 20-Questions conversation with each
provider's avatar and rate it on six statements.

Run by Anam. This repository contains the harness: the web app each
rater interacts with, the per-provider integration code, the scripts that build
and launch the rating experiment, and the published evidence bundles.

This README is operational only — how to run the study and reproduce the
results.

## Quickstart: reproduce the results

From this directory, use Python 3.10+:

```sh
python3 scripts/4_analyse.py --variant turnkey
python3 scripts/4_analyse.py --variant livekit
```

These commands read the included evidence bundles offline and need no API
keys or additional packages. Add `--check-envelopes` to either command to
also recompute reply latencies from the supplied audio envelopes. See
[Reproducing the published results](#reproducing-the-published-results)
for the study dates, bundle contents and calculation details.

## Layout

```
config/                       study configuration,
                              shared by the web app and the scripts
src/pages/digital-avatars/    one dynamic route serving every provider's
                              turnkey stimulus page, embedded by iframe
src/pages/livekit/            the same for the LiveKit study
src/components/avatars/*      provider client integrations
src/pages/api/[provider].ts   single server-side mint route (token, prompt,
                              cap, evidence manifest)
src/lib/providers/*           per-provider adapters called by the mint route
src/prompts/                  the shared 20-Questions task prompt
scripts/1_publish.py          build, deploy, smoke, dry-run, launch gate
scripts/2_experiment.py       datasets, experiment, rating job
scripts/3_bundle.py           evidence bundle from a completed job
scripts/4_analyse.py          recompute from a bundle
scripts/*                     the individual steps those four call
provider_configs/             what each provider ran on its own platform —
                              HeyGen contexts, LemonSlice agents, lineups
                              (see its README)
analysis/reproduce.py         recomputes the published figures from a bundle
analysis/reproduce_livekit.py compares the two bundles, for --variant livekit
evidence/<experiment-id>/     published data bundle, one per fielded study
agent/                        LiveKit study (separate lifecycle)
```

| Configuration file | Contents |
|---|---|
| `config/avatars.json` | the avatar slots per provider (LemonSlice adds a practice agent), their avatar/agent/replica ids, the voice id where the provider takes one, slot names, per-slot topics. A provider with no cast is not fieldable and the agent refuses a session for it. `mainSlots` is the subset each run fields and `trainingSlot` the one the practice round uses. |
| `config/providers.json` | Anam avatar model and LLM id, the avatar model each Anam arm pins, and the stimulus page path per provider |
| `config/experiment.json` | rating statements and scales, topics and their example words, the session cap, and the rater introduction |
| `config/livekit.json`, `config/livekit_tavus.json` | LiveKit study only. `pagePaths` in `livekit.json` names the providers with a LiveKit page, and the launch gate reads it from there; the arms in `providers.json` are added to them, and `--providers` picks which of the result a run fields. The two variants do not field the same set. |

## Requirements

Every command in this file runs from `studies/benchmark/`, unless it says
otherwise. `npm` commands run from the repo root, two levels up.

To recompute the published figures you need Python 3.10+ and nothing else —
see *Reproducing the published results*. The rest of this list is for fielding
a study of your own.

- Node 22.12+ and npm (`package.json` sets the engine floor) — `npm install`
  from the repo root. The checks shell out to `tsx`, which comes with it.
- Python 3.10+ — `pip install -r scripts/requirements.txt`, and the two
  packages the scripts import: `pip install -e ../../packages/stimulus -e ../../packages/mabyduck`
- `ffmpeg` and `ffprobe` on `PATH` (the launch gate decodes recordings)
- Accounts with each provider under test, and with a rating platform
- Somewhere to host the stimulus pages (see below)
- A person with a microphone and a browser. Budget about fifteen minutes.
- For the LiveKit variant only: the LiveKit CLI (`lk`), installed and
  authenticated against your LiveKit Cloud project (`brew install livekit-cli`
  or see docs.livekit.io/home/cli). The agent deploy builds a container image
  remotely and takes around fifteen minutes, printing dense buildkit output
  while it does.

## Hosting the harness

The rating platform embeds each stimulus page in a cross-origin iframe, so the
harness has to be served from a public HTTPS origin. What it needs from that
origin:

| Requirement | What it is for |
|---|---|
| Server-side rendering with API routes | provider keys are never sent to the browser; every session is minted server-side |
| A key-value store, writable from the request handler | session manifests, mint records and recording chunks are written as the session runs |
| Per-environment secrets | the provider keys and the URL-signing secret |
| Cross-origin iframe embedding | the page runs inside the rating platform |

### One way: Cloudflare Workers and KV

This repository is configured for Cloudflare: Astro's `output: 'server'` with the
`@astrojs/cloudflare` adapter, a Workers KV namespace bound as `MANIFEST`, and
`wrangler` for deploys and secrets. Create your own namespace with
`npx wrangler kv namespace create MANIFEST` from the repo root. Replace
`REPLACE_WITH_YOUR_KV_NAMESPACE_ID` in `studies/benchmark/wrangler.jsonc`
with the ID it prints, then copy the study configuration to the repo root:

```sh
# From the repo root:
cp studies/benchmark/wrangler.jsonc wrangler.jsonc
```

The build reads the root configuration; the evidence scripts read the study
configuration. Keep the namespace ID the same in both.

To run it somewhere else, swap the Astro adapter and provide a `MANIFEST`
binding with a `put(key, value)` method (`src/lib/api.ts`,
`src/pages/api/manifest.ts`, `src/pages/api/recording.ts`).

One coupling to know about: the evidence tooling reads that store back out.
`scripts/build_evidence_bundle.py` uses Cloudflare's KV REST API and
`scripts/launch_gate.py` shells out to `wrangler`. Moving hosts means
reimplementing those reads against the new store.

## Secrets and configuration

Two separate places:

- `.env` (gitignored) — read by the scripts on your machine. Copy
  `.env.example` and fill it in.
- Worker secrets — read by the deployed harness. Set once per environment with
  `npx wrangler secret put <NAME>`. They live only in Cloudflare: never
  repository secrets, never in code.

For a turnkey study the worker needs `ANAM_API_KEY`, `TAVUS_API_KEY`,
`HEYGEN_API_KEY`, `LIVEAVATAR_CONTEXT_ID_MAIN`,
`LIVEAVATAR_CONTEXT_ID_PRACTICE`, `LEMONSLICE_API_KEY` and
`BENCHMARK_URL_SECRET`.

The LiveKit study additionally needs `LIVEKIT_URL`, `LIVEKIT_API_KEY` and
`LIVEKIT_API_SECRET` as worker secrets — `/api/livekit` mints the room token
server-side — as well as in `agent/.env`.

`BENCHMARK_URL_SECRET` must be identical in `.env` and in the worker: the
scripts sign every stimulus URL with it and the worker verifies that signature
before minting a session. A mismatch produces 403s on every mint.

### Where each value comes from

| Variable | Where to get it |
|---|---|
| `ANAM_API_KEY` | Anam dashboard → API keys |
| `TAVUS_API_KEY` | Tavus dashboard → API keys |
| `HEYGEN_API_KEY` | HeyGen LiveAvatar (`api.liveavatar.com`) key, from the LiveAvatar dashboard. Not interchangeable with a classic HeyGen key |
| `LIVEAVATAR_CONTEXT_ID_MAIN`, `LIVEAVATAR_CONTEXT_ID_PRACTICE` | ids of the two contexts you create holding the task prompt. Build them from `provider_configs/heygen/CONTEXT_SPEC.md`, which gives the exact name, prompt file and empty `opening_text` for each |
| `LIVEAVATAR_API_KEY` | LiveKit study only: the key the agent's HeyGen face plugin takes. Distinct from `HEYGEN_API_KEY`, which the worker uses for the turnkey study |
| `SYNTHESIA_API_KEY` | LiveKit study only: the key the agent's Synthesia face plugin takes |
| `LEMONSLICE_API_KEY` | LemonSlice dashboard → API keys. The agents are dashboard objects, so you build your own and replace the ids in `config/avatars.json` — `provider_configs/lemonslice/AGENT_SPEC.md` gives the face, voice, settings and prompt for each |
| `MABYDUCK_API_KEY`, `MABYDUCK_PROJECT_ID` | rating platform account and the project the experiments live in |
| `MABYDUCK_RATER_POOL_ID` | the rater pool jobs recruit from. Listed in the rating platform's console under the project |
| `BENCHMARK_BASE_URL` | origin of your deployed harness, e.g. `https://<name>.workers.dev`. Printed by `npx wrangler deploy` |
| `PUBLIC_WORKER_COMMIT` | optional. Stamped into every mint record as `workerCommit`; unset, mints record `dev`. `.github/workflows/deploy.yml` sets it from the deploying commit |
| `BENCHMARK_URL_SECRET` | generate one, e.g. `openssl rand -hex 32`. Put the same value in `.env` and in the worker |
| `BENCHMARK_URL_TTL_DAYS` | optional, default 30. How long a built dataset's stimulus URLs keep minting |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare dashboard → Workers & Pages → Account ID. Used only for KV REST reads when building a bundle |
| `LIVEKIT_AGENT_PROJECT` | LiveKit Cloud project name, LiveKit study only |
| `LIVEKIT_AGENT_ID` | the deployed LiveKit agent this updates. `agent/deploy.sh` generates `agent/livekit.toml` from this and the host label of `LIVEKIT_URL`. Omit it and the deploy stops rather than creating a second agent |

The LiveKit agent reads its own `agent/.env`. Copy `agent/.env.example` and
fill it in, or keep everything in the root `.env` — the agent reads that as a
fallback. It needs `LIVEKIT_URL`, `LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET`
from LiveKit Cloud, `DEEPGRAM_API_KEY`, `GROQ_API_KEY` and `CARTESIA_API_KEY`
for the shared pipeline, and five face-layer keys: `ANAM_API_KEY`,
`TAVUS_API_KEY`, `LEMONSLICE_API_KEY`, `SYNTHESIA_API_KEY` and
**`LIVEAVATAR_API_KEY`**.

The HeyGen contexts and the LemonSlice agents are account objects. Each has a spec for building
your own — `provider_configs/heygen/CONTEXT_SPEC.md` and
`provider_configs/lemonslice/AGENT_SPEC.md` — and `provider_configs/` records
what each ran. The Tavus personas are scripted: `scripts/create_tavus_personas.py`
for the turnkey study, `agent/setup_tavus_personas.py` for the LiveKit one.

## Running a study

These four stages field a NEW study on your own accounts. They do not rebuild
the bundles published here — those came from our runs, and stage 3 reads the
rating job and the evidence store that produced them. To check our numbers
instead, go to *Reproducing the published results*: stage 4 recomputes from
the bundles in this repository.

Four stages, in order. Each takes `--variant turnkey` (every provider runs its
own full stack) or `--variant livekit` (all four share one STT/LLM/TTS
pipeline and contribute only a face layer), so the same four commands run
either study.

The LiveKit variant needs one setup step before stage 1, once per Tavus
account — see *LiveKit only, before stage 1: create the echo-mode Tavus personas* below.

| Stage | Command | Does |
|---|---|---|
| 1 | `scripts/1_publish.py` | build, deploy, smoke, dry-run, launch gate |
| 2 | `scripts/2_experiment.py` | upload datasets, create the experiment, create and launch the rating job |
| 3 | `scripts/3_bundle.py` | assemble the evidence bundle from a completed job |
| 4 | `scripts/4_analyse.py` | recompute from the bundle |

Stages 1–3 need credentials: stage 1 the provider keys and a Cloudflare
deploy; stage 2 the rating-platform keys, plus `BENCHMARK_BASE_URL` and
`BENCHMARK_URL_SECRET`, which the stimulus URLs are built from and signed
with; stage 3 the rating platform, `CLOUDFLARE_ACCOUNT_ID`, a `wrangler
login` and `ffmpeg`. Stage 4 needs none — it reads only the
bundle.

### LiveKit only, before stage 1: create the agent

`agent/deploy.sh` updates an existing LiveKit Cloud agent and refuses to run
without `LIVEKIT_AGENT_ID`. The first agent is therefore created by hand,
once:

```sh
cd agent
lk agent create --secrets-file ../../../.env   # or ./.env, if you use one
```

`lk` prints the agent id — put it in `.env` as `LIVEKIT_AGENT_ID`. The agent
runs as its own container with no access to your machine, so its provider keys
have to be injected: `--secrets-file` does that at creation, and
`lk agent update-secrets` changes them later. Without them the agent deploys
and then fails on its first job, when `agent/src/agent.py` reads
`GROQ_API_KEY`.

`lk` reads only the file you name — it has no root-`.env` fallback of its own,
unlike the agent and `deploy.sh`. It wants `KEY=VALUE` lines; pass
`--ignore-empty-secrets` if yours has keys you have left blank.

`lk agent update-secrets` **replaces the whole set**: whatever you pass becomes
the agent's secrets, and every name you leave out is deleted. `--overwrite`
governs whether it will replace a name that already exists, not whether it
keeps the others. So change one key by passing them all —
`--secrets-file ../../../.env` is the safe form, and one key at a time is how
you lose the rest. `lk agent secrets` lists the names it holds, and is worth
reading before and after.

Nothing outside that store is a backup of it, so every name the agent needs
belongs in `.env` too. `LIVEAVATAR_API_KEY` is the one that catches people out:
the LiveKit HeyGen plugin insists on that name, the turnkey harness calls the
same credential `HEYGEN_API_KEY`, and both authenticate the same way against
api.liveavatar.com.

Every later deploy goes through `agent/deploy.sh`, which re-syncs the vendored
config from `config/` and generates `livekit.toml` from your own
`LIVEKIT_URL` and `LIVEKIT_AGENT_ID`.

### LiveKit only, before stage 1: create the echo-mode Tavus personas

From `studies/benchmark`:

```sh
TAVUS_API_KEY=$(grep '^TAVUS_API_KEY=' ../../.env | cut -d= -f2-) \
    python agent/setup_tavus_personas.py --force
```

Once per Tavus account, before the first LiveKit run, and again whenever an arm
adds replicas. It creates one `pipeline_mode: "echo"` persona per replica and
writes the replica to persona map to `config/livekit_tavus.json`, covering the
base Tavus arm and every Tavus arm in `config/providers.json`.

A replica already in the map is left alone, so a run creates only what is
missing. `--dry-run` reports what it would create, from the map rather than
from the account.
`--force` recreates every persona, replacing the ids a study was fielded with;
delete the superseded ones in the Tavus dashboard.
`scripts/tavus_gc.py` will not remove them: it skips personas whose
`pipeline_mode` is not `full`, and any named in `config/livekit_tavus.json`.

The map it carries belongs to the account that produced it. If your
`TAVUS_API_KEY` is a different account, run this step: the personas it names
will not exist for you, and stage 1's conformance check is what tells you so.

### 1. Publish the harness

```sh
python scripts/1_publish.py --variant turnkey
python scripts/1_publish.py --variant livekit
```

Builds the web app, deploys the worker (and under `--variant livekit` the
LiveKit agent), smoke-tests the variant's mint routes, prints signed dry-run
URLs, and then runs the launch gate over the evidence those dry-run sessions
wrote.

`python scripts/tavus_gc.py` reports study Tavus personas that are no longer
referenced and `--delete` removes them; its help lists what it leaves alone.
Run it after a study.

The run pauses before the gate.
Open each printed URL, allow the microphone, press Start Call, and talk long
enough to record.

The pause needs a terminal. With no one attached to standard input — CI, a
pipeline, a background job — it prints `(non-interactive: continuing straight
to the gate)`, and runs the gate anyway.

`--skip-deploy` starts at the smoke test; `--gate-only` re-runs just the gate.
`--base` points the dry-run URLs at another origin.

The gate bounds how far back it looks. Stage 1 measures how long you took and
passes that window to `launch_gate.py --since-minutes`. The bound is on time alone, so a mint
from the smoke step falls inside it too — those carry no manifest or
recording, so they fail the gate rather than satisfying it, and a provider
whose line reads `no client manifest` may be reporting the smoke mint rather
than a dry run of yours. Run standalone, the
gate defaults to the last 60 minutes; `--since-minutes 0` disables the bound.

To do it all locally first, run the harness under `wrangler dev` — the same
runtime as production, with KV writes going to a local store under
`.wrangler/state` instead of the study's namespace:

```sh
npm run build && npx wrangler dev --port 8788
python scripts/1_publish.py --variant turnkey --skip-deploy --base http://localhost:8788
python scripts/inspect_local_sessions.py
```

Only the evidence store is local. `wrangler dev` serves the real provider keys
from `.env`, so the smoke step mints one billable session per provider against
the live providers, exactly as it does against a deployed worker.

`localhost` is a secure context, so the microphone works without HTTPS.

Local dry runs cannot satisfy the launch gate: their KV writes go to
`.wrangler/state`, and the gate reads the deployed namespace. Use the local
loop to check a change by hand with `inspect_local_sessions.py`, and run the
gate against dry runs held on the deployed harness.

`inspect_local_sessions.py` reports, for a local dry run, several things that
are invisible on screen, in particular whether the avatar's
first turn fired on the provider's own readiness signal or on the fallback
timer. LiveKit sessions have no kickoff, so there is nothing to inspect there.

The smoke step tests the routes the variant you passed uses: four billable
provider sessions for `--variant turnkey`; for `--variant livekit`, one
room-token mint per arm the variant fields. It also checks each route refuses an unsigned and an
expired token.

The gate runs for both variants. Its evidence checks are the same — mint,
manifest, recorder stats, reassembled recording, signal on both audio
channels — but conformance differs. For turnkey it is catalog conformance, which
`scripts/verify_provider_defaults.py` defines: the Anam model and every arm's
pinned avatar model resolve for every avatar, the Anam voices are stock and uniform, and each HeyGen
avatar's configured voice is its catalogue default. For LiveKit it is that every Tavus replica must
be driven by an echo-mode persona, and the shared pipeline must be fully
specified.

### 2. Configure and launch the experiment

```sh
python scripts/2_experiment.py --variant turnkey \
    --num-sessions 60 --num-comparisons 16 --num-training 2 \
    --providers heygen,lemonslice,anam-cara-4-1-express,tavus-phoenix-4-5
python scripts/2_experiment.py --variant livekit \
    --num-sessions 60 --num-comparisons 20 --num-training 2 \
    --providers heygen,lemonslice,synthesia,anam-cara-4-1-express,tavus-phoenix-4-5
```

Those are the published studies' parameters. Without `--providers` every arm
in `config/providers.json` is fielded, base arms included. The defaults are 10 raters, 8
comparisons and 1 training round, so omitting the flags recruits 10 people and
gives each of them half the ratings.

Builds and uploads both datasets (rated and practice), creates the experiment
over them, then creates the rating job and launches it. The ids are chained,
so none has to be copied by hand.

**This stage recruits and pays raters.** Launching is the default. The cost is
fetched and printed as part of launching, not when the job is created, so a
`--no-launch` draft never shows you one — check the cost on the rating
platform before launching a draft from there.

`--no-launch` leaves the job as a draft. `--experiment-id` skips straight to
creating a job on an experiment that already exists. `--num-sessions` sets how
many raters to recruit — it is what you are charged for. `--num-comparisons`
sets rated conversations per rater and `--num-training` the practice rounds;
both default to 8 and 1; the turnkey study ran 16 and the LiveKit study 20,
with 2 training rounds each, so pass them explicitly to reproduce the fielded
shape. `--rater-pool-id` sets the pool. `--providers` picks the arms, and
`analysis/reproduce.py` reads the arms back from the ratings, so a study with
a different arm set still analyses.

Stimulus URLs are signed with `BENCHMARK_URL_SECRET`. A dataset is only
usable against a worker holding the same secret. Publish first.

Each signed URL also carries an expiry, `BENCHMARK_URL_TTL_DAYS` days out
(default 30), after which it stops minting. Field a dataset within its
lifetime; to run one again later, rebuild it so the URLs are re-signed.

This stage writes the built datasets to `mabyduck_dataset/` in the working
tree, and stage 1 writes `manifest_data/provider_defaults.json`. Both are
generated artefacts and are gitignored.

Stage 3 needs a *completed* job. `python scripts/pull_session_meta.py
--experiment-ids <experiment-id>` writes every rater session to
`meta_out/sessions_<experiment-id>.json`, where `date_completed` per session
is what tells you the job has finished; stdout gives only a count and a
display-context summary, and the count spans every job on the experiment rather
than one. Building a bundle from a job still in progress refuses with
`refusing to write empty ratings.csv`.

### 3. Build the evidence bundle

```sh
python scripts/3_bundle.py --variant turnkey --experiment x1ecy3l --job j120nhl
python scripts/3_bundle.py --variant livekit --experiment x11dvlb --job j1zr2wm
```

Pulls the ratings from the rating platform and the session evidence from KV,
derives the audio envelopes and turn metrics, and writes the bundle. `--out`
defaults to `evidence/<experiment>`.

Expect roughly half an hour for a 60-rater study and a minute or two for
a handful of raters: it fetches and decodes every session recording, so the
time scales with sessions rather than ratings. Progress prints every hundred.

The window is derived from the job's own rater sessions, opening two minutes
before the earliest of them. `--skip-envelopes` reuses an existing
`audio_envelopes.jsonl.gz`, the only slow stage: it fetches and decodes every
session recording.

A build fails outright if a chunk is present in KV but unreadable,
`--allow-decode-failures` included, because the recording is intact and only
the run is not.

### 4. Recompute the figures

```sh
python scripts/4_analyse.py --variant turnkey
python scripts/4_analyse.py --variant livekit
python scripts/4_analyse.py --variant turnkey --check-envelopes
```

`--bundle` selects a bundle other than the variant's published one.

`--variant livekit` runs the within-bundle figures for the LiveKit study and
then the cross-study comparison against the turnkey bundle.
`--compare-with` points at a different turnkey bundle.

## How a session starts

This describes the turnkey study. Under `--variant livekit` every condition
runs one shared pipeline that opens the conversation itself, so none of the
kickoff machinery below applies and no kickoff events are recorded.

Anam's pipeline takes the opening turn by itself. For the other three the
client sends one bracketed service note (`src/lib/kickoff.ts`, one constant
shared by all three) on that platform's own readiness signal:

| Provider | Readiness signal | Kickoff |
|---|---|---|
| anam | — | none; self-starts |
| tavus | the replica's Daily `participant-joined`; Tavus's own `system.pal_joined` / `system.replica_joined` app-message is recorded, not gated on | `conversation.respond` |
| heygen | session state `CONNECTED` | `avatar.speak_response` |
| lemonslice | `bot_ready` app-message | `chat-msg` |

Tavus and HeyGen retry, up to four attempts, until the avatar is heard
speaking; Tavus also sends the note on a timer if its readiness signal
never arrives. LemonSlice sends one note, on `bot_ready`. Which path fired
and how many attempts it took is recorded per session in
`session_events.csv`.

## Provider-side configuration

`config/` and `src/lib/providers/` hold everything the harness sends. HeyGen
and LemonSlice also hold configuration on their own platforms — HeyGen's task
in dashboard contexts, LemonSlice's in dashboard agents — exported to
`provider_configs/` along with each fielded lineup.
See `provider_configs/README.md`.

## Reproducing the published results

If you need help reproducing anything, contact support@anam.ai.

This section is the check on our numbers, and it needs nothing but this
repository: no accounts, no keys, no network. Everything under *Running a
study* is the other thing — fielding your own study, with your own raters and
your own provider accounts.

Two studies are published, one bundle each:

| Bundle | Study | Fielded |
|---|---|---|
| `evidence/x1ecy3l/` | turnkey — each provider ran its own full stack | 2026-10-16 |
| `evidence/x11dvlb/` | LiveKit — one shared STT/LLM/TTS pipeline, provider face layer only | 2026-10-16 to 10-17 |

The Anam arm is recorded in both bundles as `anam-cara-4-11`, its mints
carrying `avatarVersion: cara-4-11`. `config/providers.json` names the same
model `cara-4.1-express`.

| File | One row per |
|---|---|
| `ratings.csv` | rated conversation: six statements on −2…+2 scales |
| `rater_sessions.csv` | rater session: public codes, age band and reduced technical/demographic context |
| `session_evidence.csv` | avatar session: connect time, delivered resolution/fps samples, recording inventory, connectivity probe |
| `ratings_evidence_join.csv` | the rating-to-session join, with the match gap in seconds |
| `server_manifests.csv` | mint record: what was requested from each provider |
| `session_turn_metrics.csv`, `turn_latencies.csv` | per-session and per-turn timing |
| `recording_chunks.csv` | one uploaded chunk of a session recording: sequence, offset, size |
| `audio_envelopes.jsonl.gz` | per-session loudness envelope in 20 ms frames per channel — the timing substrate behind the latency figures |
| `conditions.csv`, `study_config.json` | the condition grid and the study configuration, generated from `config/` when the bundle is built |
| `demographics_aggregate.json` | rater demographic counts, aggregated |
| `session_events.csv` | one recorded event of one session. The turnkey bundle carries the mint, transport and participant milestones and the first-turn readiness path and attempts; the LiveKit bundle carries the connectivity probe and first media, since that study has no kickoff |
| `evidence_bundle.xlsx` | the same CSVs as one workbook, plus the two JSON files as sheets |

Both bundles carry `session_events.csv`. Some `session_evidence.csv` columns
are empty throughout one bundle or the other. In `evidence/x11dvlb`:
`error_detail`, `kickoff_via`, `ice_error`, `avatar_or_replica_id`, `voice_id`
and `model`. In `evidence/x1ecy3l`: `ice_error` and `slot`. Per-slot analysis
comes from `ratings.csv`, which carries the slot on every row.

### Recomputing

`analysis/reproduce.py` recomputes from a bundle. It is standard library
only — no dependencies, no network, no credentials:

```sh
python scripts/4_analyse.py --variant turnkey
python scripts/4_analyse.py --variant livekit
python scripts/4_analyse.py --variant turnkey --check-envelopes
```

`scripts/BUNDLE_FORMAT.md` describes what a bundle contains, and its
*Derived values* section defines the per-rater normalisation, the
speech-detection thresholds and the reply-latency rule. The resampling,
the significance threshold and the exclusion screens are in
`analysis/reproduce.py`.
`analysis/reproduce.py` is the underlying script and can be run directly
against any bundle with `--bundle`. The LiveKit variant additionally runs
`analysis/reproduce_livekit.py`, which takes two bundles as `--default-stack`
(the turnkey study) and `--normalised-stack` (the LiveKit study) rather than
one `--bundle`, and labels its output with those two names.

The analysis covers the headline and its bootstrap confidence intervals, the
per-statement and per-avatar breakdowns, raw versus normalised scoring,
connection times, turn latency, the perception correlations, the demographic
subgroups, and the exclusion sensitivity table. Turnkey takes a minute or two and
`--check-envelopes` adds a few seconds; LiveKit runs two scripts and takes
three to five, depending on the machine.
Bootstrap figures use a fixed seed, so repeated runs agree exactly.

`--check-envelopes` re-derives every turn latency from `audio_envelopes.jsonl.gz`
using the published thresholds, and reports how many sessions differ from
`turn_latencies.csv`.

The harness records a presentation id shared by the rating and the session,
so `ratings_evidence_join.csv` marks those rows `exact` — 927 of 960 in
`evidence/x1ecy3l` and 1,178 of 1,200 in `evidence/x11dvlb`. The rest are
`none`, where no session carries the rating's presentation id.

`ratings_evidence_join.csv` also grades a bundle whose sessions carry no
presentation id, where the join is a heuristic: `unique` where one session
matched on provider, topic, window and device fingerprint, `probable` where the
rating had exactly one candidate session in its window, `ambiguous` where it had
more than one, `none` where the row went unassigned. `scripts/BUNDLE_FORMAT.md`
has the full definitions. Neither published bundle uses those grades.

`analysis/reproduce.py --unique-attribution` drops the session link from every
rating the evidence does not force, keeping only `exact` and `unique`, and
reruns. Every joined row in both published bundles is `exact`, so the figures
are unchanged by it.

### Rebuilding a bundle from source

A bundle is rebuilt with stage 3, which runs the whole pipeline — rating
platform, KV evidence store, session recordings:

```sh
python scripts/3_bundle.py --variant turnkey --experiment x1ecy3l --job j120nhl
python scripts/3_bundle.py --variant livekit --experiment x11dvlb --job j1zr2wm
```

It needs credentials (`.env` plus a `wrangler login`) and `ffmpeg` — and it
reads the rating job and the evidence store that produced these bundles, so
it reproduces them only from inside the account that ran them. With your own
accounts it builds a bundle from your own study.

Two guards protect the audio envelopes. A recording that fails to decode is
fatal rather than a silent gap, unless `--allow-decode-failures` is passed.
Writing into a directory that already holds a bundle requires `--force`,
unless `--skip-envelopes` is passed, so the commands above refuse to run
against `evidence/x1ecy3l` and `evidence/x11dvlb` as they stand.
`scripts/3_bundle.py` accepts both flags.

If a rebuild reports decode failures, retry before reaching for
`--allow-decode-failures`: fetching thousands of chunks is the failure-prone
part. Run one rebuild at a time.

A rebuild writes the data tables and `evidence_bundle.xlsx` together. A rerun
targeting a directory that already holds a bundle needs no `--force` when
`--skip-envelopes` is passed: that leaves the audio envelopes alone. To
rebuild only the workbook from files
already on disk:

```bash
python scripts/build_evidence_bundle.py --workbook-only --out evidence/x1ecy3l
```

Audio envelopes record when sound occurred. Session recordings are not published.

Participant metadata is reduced to protect privacy. Platform rater, session and
rating IDs are replaced with export-specific codes, using the same codes across
both studies. Individual ethnicity, nationality, birthplace, language, student
status and detailed device information are omitted. Ages are banded, audio
devices are reduced to headphones/speakers, and calendar timestamps retain dates
only. Aggregate demographic counts are retained.

For a new export, generate one temporary `BENCHMARK_EXPORT_KEY` (at least 32
random bytes, hexadecimal), reuse it for both study builds, then discard it.
The builder computes joins in a private temporary directory before reducing
the records and writing the public bundle. Existing joins, scores and measured
durations are retained; device-fingerprint analyses are skipped.

## The steps behind the stages

Each is runnable on its own when only part of a stage needs redoing. Stage 2
chains `create_mabyduck_dataset.py`, `create_mabyduck_experiment.py` and
`create_mabyduck_job.py`, any of which can be run alone; stage 3 wraps
`build_evidence_bundle.py` and stage 4 wraps `analysis/reproduce.py`.

| Script | Checks |
|---|---|
| `scripts/launch_gate.py` | per-provider session evidence is complete and both audio channels are live; fails stage 1 otherwise. It does not gate stage 2. `--variant livekit` swaps catalog conformance for echo-mode Tavus personas and a fully specified shared pipeline |
| `scripts/verify_provider_defaults.py` | pinned config still matches the live provider catalogs. `--sweep-anam-sessions` additionally verifies, from Anam's own API, which model and LLM every fielded session ran |
| `scripts/smoke_worker.py` | mint route responds, and rejects unsigned requests |
| `scripts/local_stimulus_urls.py` | builds signed stimulus URLs for hand-testing, from the same config and signing code the dataset uses |
| `scripts/inspect_local_sessions.py` | replays a local dry run's session records: kickoff path, presentation id, connectivity, failure stage |
| `scripts/check_prompt_parity.sh` | the LiveKit agent's Python port of the task prompt renders byte-identically to the TypeScript original, for two topics x two round modes |
| `scripts/check_vendored_config.sh` | `agent/config/` matches the repo-root `config/` the rest of the study reads |
| `scripts/check_tavus_persona_prompts.sh` | each recorded Tavus persona's system prompt matches the task prompt rendered for its slot |
| `scripts/create_tavus_personas.py` | creates the turnkey study's Tavus personas, one per replica and round mode |
| `tools/check_evidence_payload.sh` | every stimulus URL the dataset builder emits re-derives, from the URL alone, the payload its own signature covers |

The launch gate does not check that each provider's avatar takes the first
turn — verify that by hand in the dry-run sessions of stage 1. In
`session_events.csv` a turnkey session shows `kickoff-sent` with a detail
naming the signal it fired on, and `retry` where the first one went
unanswered. LiveKit
sessions have no kickoff at all: the shared pipeline opens the conversation
itself, so there is nothing to check beyond hearing the avatar speak first.

## The LiveKit study

`agent/` holds a LiveKit agent: one shared
STT/LLM/TTS pipeline, per-session avatar provider. It has its own dependencies
(`agent/pyproject.toml`) and configuration (`config/livekit.json`).

`scripts/1_publish.py --variant livekit` deploys it via `agent/deploy.sh`,
which syncs the vendored configuration into the image and asserts it is
present.

The pipeline is in `evidence/x11dvlb/study_config.json` (`livekit_pipeline`),
generated from `config/livekit.json`. What each session actually ran is in
`server_manifests.csv`, which the worker writes per mint.
