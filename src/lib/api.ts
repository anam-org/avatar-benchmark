import { env as workerEnv } from "cloudflare:workers";

/**
 * Read a server-side secret at RUNTIME from the Cloudflare worker env
 * (Astro Cloudflare adapter: `import { env } from "cloudflare:workers"`; wrangler
 * worker secrets land there). Never read import.meta.env here: Vite
 * materialises the whole env object at BUILD time, which puts every value in
 * .env into deployed worker code. Build-time constants go through the `define`
 * in astro.config.mjs instead.
 */
export const getEnv = (name: keyof WorkerSecrets): string | undefined =>
  workerEnv[name];

export const json = (status: number, body: unknown): Response =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

export const jsonError = (status: number, error: string): Response =>
  json(status, { error });

/** 500 when a required server-side env var is missing. */
export const missingEnv = (name: string): Response =>
  jsonError(500, `Missing ${name} on server`);

/** 502 with upstream detail when a provider API call fails. */
export const upstreamError = async (
  what: string,
  res: Response,
): Promise<Response> => {
  const detail = await res.text().catch(() => res.statusText);
  return jsonError(502, `${what}: ${detail}`);
};

/**
 * Evidence capture: write a study-manifest record to the MANIFEST KV
 * namespace. Fire-and-forget.
 */
export const recordManifest = async (
  kind: string,
  id: string,
  record: Record<string, unknown>,
): Promise<void> => {
  try {
    await workerEnv.MANIFEST?.put(
      `${kind}:${Date.now()}:${id}`,
      JSON.stringify({
        ...record,
        workerCommit: WORKER_COMMIT,
        recordedAt: new Date().toISOString(),
      }),
    );
  } catch (err) {
    console.error("manifest write failed:", err);
  }
};

const constantTimeEqual = (a: string, b: string): boolean => {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
};

/**
 * Capability-token gate. The dataset builder signs each stimulus URL, and
 * the token it produces is '<expiry>.<mac>', where mac is
 * HMAC-SHA256(BENCHMARK_URL_SECRET, payload + ':' + expiry) truncated to 32
 * hex chars. Pages forward the token to their API route; routes verify
 * before minting provider sessions, and evidence routes verify before
 * accepting writes.
 *
 * FAILS CLOSED: no secret configured, no token, a malformed token, or a
 * passed expiry all mean no mint.
 */
export const verifyUrlToken = async (
  token: unknown,
  payload: string,
): Promise<boolean> => {
  const secret = workerEnv.BENCHMARK_URL_SECRET;
  if (!secret) return false; // fail closed
  if (typeof token !== "string" || !token) return false;

  const dot = token.indexOf(".");
  if (dot < 1) return false;
  const expires = Number(token.slice(0, dot));
  const presented = token.slice(dot + 1);
  if (!Number.isSafeInteger(expires) || presented.length !== 32) return false;

  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const mac = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(`${payload}:${expires}`),
  );
  const hex = [...new Uint8Array(mac)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  if (!constantTimeEqual(hex.slice(0, 32), presented)) return false;
  return Date.now() / 1000 < expires;
};

declare const __WORKER_COMMIT__: string;

/** Git commit of the deployed worker (baked at build; see deploy.yml). */
export const WORKER_COMMIT = __WORKER_COMMIT__;
