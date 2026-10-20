# LemonSlice agents — Anam-owned

LemonSlice holds the whole per-condition configuration in dashboard *agents*:
the face, the voice, the LLM and the task prompt. `/api/lemonslice` sends only
`agent_id`.

The agents fielded here live in an Anam-owned LemonSlice account. Their ids
are in `config/avatars.json` under `providers.lemonslice`. To run this
condition you create your own and replace those ids.

## What to create

One agent per scored slot plus one practice agent, each built from a
LemonSlice catalogue template. The face, the voice and the system prompt were
set; the settings below are what each agent carried when it was pulled back.

| Slot | Face | `voice_id` | `voice_speed` | `llm` | `aspect_ratio` | `idle_timeout` | `max_conv_dur` | `turn_eagerness` |
|---|---|---|---|---|---|---|---|---|
| `f2` | Andrea | `9EU0h6CVtEDS6vriwwq5` | 0.92 | `qwen36-35b-a3b` | `2x3` | 60 | 300 | `NORMAL` |
| `f3` | Amy | `FUfBrNit0NNZAwb58KWH` | 0.86 | `qwen36-35b-a3b` | `2x3` | 60 | 300 | `NORMAL` |
| `m2` | Steve | `FMQtISLdv5RvjpHBgf60` | 1 | `qwen36-35b-a3b` | `2x3` | 60 | 300 | `NORMAL` |
| `m3` | Dan | `fvVBPXuE7f1iX3dZLKFy` | 1 | `qwen36-35b-a3b` | `2x3` | 60 | 300 | `NORMAL` |

Plus a practice agent carrying the practice prompt, topic `transportation`.
Its settings are not in `agents.json`, which holds the scored agents.

Common to all four scored agents: `ignore_default_personality: false`,
`allow_image_edits: false`, `model_override: null`, `turn_timeout: -1`.
`language_code` is null on three and `en` on m2.

The faces are LemonSlice catalogue faces. `selected_faces.json` records the
face label and the agent id per slot.

## System prompts

One file per scored agent in this folder:

```
f2_agent_<id>_system_prompt.txt   … m3_agent_<id>_system_prompt.txt
```

Each is `src/prompts/twentyQuestions.ts` rendered for that agent's name, topic
and round mode. The topic is baked in per agent.

Paste the file for the slot into the agent's system prompt.

## Then

1. Put your agent ids into `config/avatars.json` under
   `providers.lemonslice`, replacing the ids checked in. Give each scored
   agent the `imageUrl` of the face you chose: the LemonSlice-hosted
   image URL the API returns as `image_url` when you create the agent. The
   LiveKit plugin takes a URL rather than a face id. The practice agent
   needs none.
2. Pull the agents back from the API and check the deployed prompt against the
   `.txt` file for that slot, ignoring blank lines. The `.txt` files are
   `src/prompts/twentyQuestions.ts` rendered with the slot's study name from
   `config/avatars.json` and its topic.
3. `python scripts/1_publish.py --variant turnkey` — the smoke step mints one
   LemonSlice session and fails loudly if an id is wrong.
