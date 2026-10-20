/**
 * The provider integration contract.
 *
 * One shared route (src/pages/api/[provider].ts) owns: request validation,
 * the stimulus capability token, prompt composition (including round-mode
 * logic), the session cap, and evidence capture. An adapter receives those
 * as inputs and contributes ONLY the provider API call.
 */

export type MintContext = {
  /** Raw (parsed) request body, for provider-specific fields. */
  body: Record<string, unknown>;
  /** The provider-side id (body[idField]) — already token-verified. */
  id: string;
  topic: string;
  name: string;
  mode: "main" | "practice";
  /** Full 20 Questions system prompt for this topic/name/mode. */
  systemPrompt: string;
  capSeconds: number;
  sessionUuid: string;
};

export type Mint = {
  /** JSON body returned to the client component. */
  response: Record<string, unknown>;
  /** Provider-specific evidence fields, merged into the server manifest. */
  manifest: Record<string, unknown>;
};

export type ProviderAdapter = {
  /** Body field carrying the provider-side avatar/agent/replica id. */
  idField: string;
  /** Override the signed payload (default built in src/pages/api/[provider].ts). */
  tokenPayload?: (body: Record<string, unknown>, id: string) => string;
  /** The provider API call. Return a Response for provider-side errors. */
  mint: (ctx: MintContext) => Promise<Mint | Response>;
  /** Optional DELETE handler (early session cleanup). */
  remove?: (body: Record<string, unknown>) => Promise<Response>;
};
