# LiveAvatar contexts — Anam-owned

Two contexts, one per round mode, each carrying the full 20 Questions
prompt verbatim (generated from `src/prompts/twentyQuestions.ts` with
`${topic}`/`${name}` left as LiveAvatar dynamic-variable placeholders).

Create each with `POST https://api.liveavatar.com/v1/contexts`, authenticated
with an `X-API-KEY` header, sending `name`, `prompt` and `opening_text`
(docs.liveavatar.com/api-reference/contexts/create-context). Use curl. The response
carries the new context's `id` under `data`. The fielded ids are
main = 301282d4-b9b3-4ce2-b5a3-ffd51a2f5208, practice =
33069685-3463-4048-b24e-43e3992cfb0f, and their pulled records are
context_main.json / context_practice.json. The spec:

1. **Name**: `Anam avatar benchmark — 20 Questions (main, no opening text)`
   - **Opening text**: empty string
   - **Links**: none
   - **Prompt**: contents of `context_prompt_main.txt` (this folder)
2. **Name**: `Anam avatar benchmark — 20 Questions (practice, no opening text)`
   - **Opening text**: empty string
   - **Links**: none
   - **Prompt**: contents of `context_prompt_practice.txt` (this folder)

Dynamic variables per session (supplied by /api/heygen): `${topic}` and
`${name}`. No `${mode}` — the mode is baked into which context the route
selects (`training` → practice context).

Then:
- put both ids in `.env` as `LIVEAVATAR_CONTEXT_ID_MAIN` and
  `LIVEAVATAR_CONTEXT_ID_PRACTICE` (worker secrets at deploy);
- verify the created prompt matches the files byte-for-byte with
  `GET /v1/contexts/{id}`, and commit the pulled records as
  `context_main.json` / `context_practice.json`.
