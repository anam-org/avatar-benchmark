# Provider-side configuration

HeyGen and LemonSlice hold part of their configuration on their own platform
rather than in this repository: HeyGen takes agent instructions through a
dashboard *context*, LemonSlice through dashboard *agents*. This directory is
the export of what each ran, pulled from their APIs. The HeyGen contexts and
the LemonSlice agents carry the platform's own `created_at`/`updated_at`;
`verification/provider_defaults.json` carries the `retrievedAt` written by the
script that pulled it.

Removed from the LemonSlice export: account fields `owner_id`,
`sufficient_balance`, `env`, `allowlist`, `is_deleted`, `is_external`; and the
catalogue-template fields the study did not use — `first_video_url`,
`first_video_script`, `is_first_video_uploaded`, `is_first_video_stale`,
`text_prompt`, `idle_text_prompt`, `docs`, `background_image_url`,
`is_enabled`, and LemonSlice's internal numeric `id`.

## heygen/

| File | Contents |
|---|---|
| `context_prompt_main.txt` | the main-round context prompt, verbatim |
| `context_prompt_practice.txt` | the practice-round context prompt |
| `context_main.json`, `context_practice.json` | the context objects as the API returns them, including `opening_text` and required dynamic variables |
| `CONTEXT_SPEC.md` | how the contexts are built |

`context_prompt_main.txt` is `src/prompts/twentyQuestions.ts` rendered for the
main round. `${topic}` and
`${name}` are platform dynamic variables, substituted per session.

Both contexts carry `opening_text: ""`.

## lemonslice/

| File | Contents |
|---|---|
| `agents.json` | the scored agents' settings pulled live from the LemonSlice API, including `system_prompt`, `model_override`, `llm`, `voice_id`, `voice_speed`, `aspect_ratio`, `ignore_default_personality`, `allow_image_edits`, `idle_timeout`, `max_conv_dur`, `turn_eagerness`. Prompts carry `updated_at` 2026-07-26T14:04. The practice agent is not included; its prompt is in the `.txt` files. |
| `<slot>_agent_<id>_system_prompt.txt` | each scored agent's system prompt, one per slot |
| `selected_faces.json` | the catalogue face label chosen for each slot, with the agent id that slot was fielded with |

The agents live in an Anam-owned account, created from LemonSlice's own
catalogue templates.

## tavus/ and anam/

Both configure per session over the API, so the fielded configuration is in
`config/` and `src/lib/providers/`. `study_replicas.json` and
`study_avatars.json` record the replicas/avatars fielded per slot;
`study_replicas.json` also carries each replica's model, and
`study_avatars.json` each Anam avatar's pinned model, active version and
voice.

## verification/

`provider_defaults.json` — output of `scripts/verify_provider_defaults.py`:

- per Anam avatar, the pinned model against the live catalogue's
  `activeVersion` and `availableVersions`
- per Anam voice, whether it is a stock catalogue voice and its provider and
  model
- per HeyGen avatar, the configured voice against the catalogue
  `default_voice`

`scripts/verify_provider_defaults.py` writes a fresh dump to
`manifest_data/provider_defaults.json`; the copy here was taken from that
path.
