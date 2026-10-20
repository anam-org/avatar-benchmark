/**
 * Stimulus capability token payloads — the worker mirror of
 * packages/stimulus/stimulus/token.py.
 *
 * The payload is built in two stages:
 *
 *   corePayload(...)     the six fields every study binds, in fixed order
 *   bindPayload(core, …) study-specific fields, appended after the core
 *
 * A study reaches the payload only through bindPayload, which can add
 * segments and nothing else. Dropping a core field, reordering the core,
 * or making one optional takes an edit to corePayload itself, which is
 * asserted identical to the Python side by tools/check_token_parity.sh.
 */

// The order is the contract. Both ends join on ":" and neither may vary it.
export const CORE_FIELDS = [
  "provider",
  "id",
  "topic",
  "mode",
  "voice",
  "model",
] as const;

export function corePayload({
  provider,
  id,
  topic,
  mode,
  voice = "",
  model = "",
}: {
  provider: string;
  id: string;
  topic: string;
  mode: string;
  voice?: string;
  model?: string;
}): string {
  return [provider, id, topic, mode, voice, model].join(":");
}

/**
 * Append study-specific segments to a core payload. Empty segments are
 * dropped rather than bound as empty strings, so a study that binds
 * nothing produces exactly the core payload and URLs signed without
 * extras stay valid.
 */
export function bindPayload(core: string, ...extra: string[]): string {
  return [core, ...extra.filter(Boolean)].join(":");
}
