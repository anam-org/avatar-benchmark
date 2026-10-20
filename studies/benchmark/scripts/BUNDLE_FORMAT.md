# Evidence bundle format

What `scripts/build_evidence_bundle.py` writes. One bundle per fielded job, at
`evidence/<experiment-id>/`.

## Files

| File | One row per |
|---|---|
| `ratings.csv` | rated conversation |
| `rater_sessions.csv` | rater session |
| `session_evidence.csv` | avatar session, recorded in the browser |
| `session_events.csv` | recorded event within a session; written when any session recorded events |
| `ratings_evidence_join.csv` | rating, matched to the session it scored |
| `session_turn_metrics.csv` | avatar session, derived from the envelopes |
| `turn_latencies.csv` | measured reply latency |
| `recording_chunks.csv` | uploaded A/V recording chunk (5 s each) |
| `server_manifests.csv` | server-side session-creation record |
| `conditions.csv` | provider × avatar slot fielded. Only the casts this study ran: an arm that shares another's cast, as every Anam arm does, appears under that cast's name, and a provider the study did not field is absent |
| `audio_envelopes.jsonl.gz` | JSON object per session |
| `study_config.json` | — question wording, scales, topics and their example words, the practice topic, the per-slot topics, the session cap, the Anam base model, the rater introduction, and for the LiveKit variant the pipeline configuration. Generated from `config/` when the bundle is built, so it records the configuration, not the run; `server_manifests.csv` records what each session ran |
| `demographics_aggregate.json` | — counts for eight of the `rater_sessions` demographic columns |
| `evidence_bundle.xlsx` | — every CSV above as one workbook, plus the two JSON files as sheets |

Every table covers the scored rounds.

## Columns

`ratings.csv` — `slate_id, is_training, rater_id, rater_session_id,
date_updated_utc, topic, slot, provider, stimulus, completion_time_ms,
presentation_id`, then the six statements: `visual_quality, naturalness,
lip_sync_quality, interruptibility, response_time, overall_experience`, each
−2..+2.

`rater_sessions.csv` — `rater_session_id, rater_id, completion_time_s`;
reduced client context `os_name, browser, platform_type, audio_device,
client_version`; `age` (18-29, 30-39, 40-49 or 50+), `sex, employment,
country_of_residence`. Audio devices are headphones/speakers flags.

Platform rater, rater-session and slate IDs are replaced by export-specific
HMAC-SHA256 codes. Related bundles use the same temporary key to preserve
cross-study pairing; the key is not published. Individual ethnicity, nationality,
birthplace, language, student status, detailed device metadata and exact calendar
timestamps are not published. Aggregate demographics are retained separately.

`session_evidence.csv` — one row per avatar session: the browser writes its
manifest on teardown and the beacon can fire more than once, so the latest
wins. `session_uuid, provider, arm, presentation_id,
recorded_at_utc, duration_seconds (first frame to session end), connect_ms, mint_ms, end_state, error_detail,
kickoff_via, last_event`; connectivity probe `ice_candidate_types, ice_srflx,
ice_srflx_ms, ice_gather_ms, ice_error, net_effective_type, net_rtt,
net_protocols, net_downlink, worker_colo, recorder_start_ms` — `net_protocols`
is the transport actually negotiated, `worker_colo` the edge that served the
mint, and `recorder_start_ms` how long the recorder took to start; stimulus
`slot, topic, avatar_or_replica_id, voice_id, model` (which of these carry
values differs by study); delivered video
`res_h_t2, res_h_t15, res_h_t60, res_h_last, res_w_t15, res_w_t60, res_w_last,
fps_t15, fps_t60, fps_last, last_sample_t, dropped_frames_last,
total_frames_last`; recording `recorded, n_recording_chunks,
recording_bytes` — both from the client's own manifest, which it sends on
teardown, so a session that ended mid-interval under-reports by its last
partial chunk. KV holds the complete recording; the envelopes are built from
KV, not from these two counts. Display and viewport dimensions, device pixel
ratio and user-agent strings are omitted from the public bundle.

`session_events.csv` — `session_uuid, provider, t_ms` (from the Start Call click),
`event, detail`. Grouped by session; ordered by `t_ms` within a session.

`ratings_evidence_join.csv` — `slate_id, session_uuid, gap_s, confidence,
n_candidates`.

`session_turn_metrics.csv` — `session_uuid, provider, arm, duration_s`,
`n_user_turns, n_avatar_turns, user_talk_s, avatar_talk_s,
n_measured_replies, unanswered_turns, median_latency_s`. `duration_s` is the
envelope length.

`turn_latencies.csv` — `session_uuid, provider, arm, turn_index, latency_s`.

`recording_chunks.csv` — `session_uuid, seq, at_ms, bytes`.

`server_manifests.csv` — `provider, ts_utc, session_uuid`, then whatever the
provider's mint recorded: `agentId, avatarId, avatarVersion, capSeconds,
contextId, conversationId, error, llmId, maxCallDuration, mintFailed,
mintUpstreamMs, mode, model, personaId, recordedAt, replicaId, sessionId,
status, tokenIssued, topic, voiceId, workerColo, workerCommit`. Fields a
provider does not use are empty. A LiveKit mint records `provider, ts_utc,
session_uuid, avatarVersion, mintUpstreamMs, mode, pipeline, recordedAt,
roomName, slot, topic, voiceId, voiceName, workerColo, workerCommit`. A row
with `mintFailed` set is a mint the provider refused; `error` carries what
it said. `workerCommit` is the harness revision the worker was built from, as
recorded at build time.

`conditions.csv` — `provider, slot, avatar_name, avatar_or_replica_id`, plus
`voice_id` for the turnkey variant. The LiveKit variant has no `voice_id`
column; its per-session voice is in `server_manifests.csv`.

`audio_envelopes.jsonl.gz` — one JSON object per session whose recording
chunks were still in the evidence store when the bundle was built: `session_uuid, provider, status, hz`, and dBFS in 20 ms frames per
channel, `mic_db` (rater microphone) and `avatar_db` (avatar audio).

## Joins

`ratings` → `rater_sessions` on the public `rater_session_id` code.

Joins are computed before metadata reduction. Published timestamps retain dates
only; scores, join assignments, durations and relative timing remain unchanged.
The individual-device attribution analysis is unavailable in public exports.

`ratings` → `session_evidence` via `ratings_evidence_join.csv`. Where the
harness mints a presentation id, the rating and the session both carry it and
the join matches on it. Where it does not, the join runs in rating-time order:
a candidate session must fall inside the rater's window
(`date_created` − 2 min .. `date_completed` + 5 min), match the rating's
provider and topic, have been recorded no later than 90 s after the rating was
submitted, and not already be assigned. The latest remaining candidate is
chosen.

`confidence` records which applied:

| value | meaning |
|---|---|
| `exact` | rating and session carry the same presentation id |
| `unique` | one session matched provider, topic, the rater's window and their device fingerprint, and no other rater on the same device overlapped that window on the same provider and topic |
| `probable` | the rating had exactly one candidate session in its window |
| `ambiguous` | it had more than one; the row names the latest unassigned one |
| `none` | no candidate left after the already-assigned and 90 s rules; `session_uuid` empty |

`n_candidates` counts the sessions whose `provider` column equals the
rating's, and that match on topic, window and presentation-id eligibility,
before the 90 s bound and the already-assigned rule are applied. Every other table joins
to `session_evidence` on `session_uuid`, except `server_manifests.csv`: a mint
whose browser never reported back has a manifest and no session row. The provider is recorded on the rating
itself, independently of the join.

## Derived values

- **Scores.** Per-rater z-normalisation: for each rater and axis, subtract that
  rater's mean and divide by their population SD across their scored slates,
  then average axes per slate. An SD below 0.3 is replaced by 1.0.
- **`arm`.** The condition fielded, and the column to join and group on. It
  differs from `provider` when a vendor fields more than one arm — a pinned
  Anam model, or a second Tavus model. `provider` is the vendor in
  `evidence/x1ecy3l` and the arm in `evidence/x11dvlb`. The client manifest names only the vendor, so the
  arm is resolved from what the mint recorded: the avatar model an Anam arm
  pins, the replica a Tavus arm casts. Equal to `provider` for a base arm.
  `ratings.csv` carries the arm in its `provider` column, since a rating is
  addressed to the stimulus path.
- **`connect_ms`.** Start-click to the first frame painting in the session's
  video element.
- **`mint_ms`.** Upstream time the provider's own API took to mint the
  session, joined from that session's server manifest. A component of
  `connect_ms`, not a separate window; `connect_ms - mint_ms` is the
  transport and first-paint part.
- **Speech.** Frames above −55 dBFS lasting ≥ 0.30 s. Bursts less than 0.80 s
  apart merge into one turn.
- **Reply latency.** For a user turn ending at *t*, the candidate reply is the
  first avatar onset later than *t* + 0.05 s. Discarded if the avatar was
  already speaking at *t*, or if the user starts another turn before that
  onset. Recorded when the onset is at least 0.1 s after *t*.
- **`unanswered_turns`.** User turns after which no avatar speech began at
  all, excluding turns ending within the last 3.0 s of the envelope. A turn
  where the avatar was already speaking, or where the user spoke again before
  the avatar did, counts as neither a reply nor unanswered.
- **Failed sessions.** A session that never connected appears in
  `session_evidence` with no `connect_ms` and zero duration. `end_state`
  records the transport state, not whether video arrived: a session can reach
  the provider's connected state and still deliver no frame.
