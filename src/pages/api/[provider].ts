import type { APIRoute } from "astro";
import experimentConfig from "../../config/experiment.json";
import { json, jsonError, recordManifest, verifyUrlToken } from "../../lib/api";
import { PROVIDERS } from "../../lib/providers";
import { buildPrompt } from "../../prompts";
import { corePayload } from "../../lib/token";

export const prerender = false;

/**
 * The single session-mint route.
 *
 * Owns:
 * - stimulus capability token verification (signed dataset URLs only);
 * - the task prompt, composed HERE (src/prompts);
 * - the session cap, passed to every adapter and sent upstream by the two
 *   providers whose APIs take one;
 * - the server-side evidence manifest, written for every mint.
 */
export const POST: APIRoute = async ({ params, request }) => {
  const adapter = PROVIDERS[params.provider ?? ""];
  if (!adapter) return jsonError(404, "unknown provider");

  // `request.cf` is absent when running locally, hence the optional chain.
  const cf = (request as Request & { cf?: { colo?: string } }).cf;

  const body = (await request.json().catch(() => ({}))) as Record<
    string,
    unknown
  >;
  const id = typeof body[adapter.idField] === "string"
    ? (body[adapter.idField] as string).trim()
    : "";
  if (!id) return jsonError(400, `${adapter.idField} is required`);
  const topic = typeof body.topic === "string" ? body.topic : "";
  if (!topic) return jsonError(400, "topic is required");
  const sessionUuid =
    typeof body.sessionUuid === "string" ? body.sessionUuid : "";
  if (!/^[a-f0-9-]{10,40}$/.test(sessionUuid)) {
    return jsonError(400, "sessionUuid required");
  }

  const mode = body.training ? "practice" : "main";
  // The token binds the stimulus parameters listed in lib/token.ts, so a
  // signed URL cannot be replayed with a different topic, mode, voice or
  // model. The core six come from lib/token.ts, mirrored in
  // packages/stimulus; an adapter may substitute its own payload.
  const payload = adapter.tokenPayload
    ? adapter.tokenPayload(body, id)
    : corePayload({
        provider: params.provider ?? "",
        id,
        topic,
        mode,
        voice: (body.voiceId as string) ?? "",
        model: (body.model as string) ?? "",
      });
  if (!(await verifyUrlToken(body.k as string | undefined, payload))) {
    return jsonError(403, "invalid or missing token");
  }
  const name = typeof body.name === "string" && body.name ? body.name : "Avatar";
  const capSeconds = experimentConfig.sessionCapSeconds;

  const mintStartedAt = Date.now();
  const minted = await adapter.mint({
    body,
    id,
    topic,
    name,
    mode,
    systemPrompt: buildPrompt({
      topic,
      name,
      training: mode === "practice",
    }),
    capSeconds,
    sessionUuid,
  });
  // Upstream time for this provider's mint call.
  const mintUpstreamMs = Date.now() - mintStartedAt;
  if (minted instanceof Response) {
    // Mint failures leave evidence too.
    await recordManifest(`server-${params.provider}`, sessionUuid, {
      sessionUuid,
      topic,
      mode,
      mintFailed: true,
      status: minted.status,
      error: (await minted.clone().text()).slice(0, 500),
      mintUpstreamMs,
      workerColo: cf?.colo ?? null,
    });
    return minted;
  }

  await recordManifest(`server-${params.provider}`, sessionUuid, {
    sessionUuid,
    topic,
    mode,
    ...minted.manifest,
    mintUpstreamMs,
    workerColo: cf?.colo ?? null,
  });

  return json(200, minted.response);
};

export const DELETE: APIRoute = async ({ params, request }) => {
  const adapter = PROVIDERS[params.provider ?? ""];
  if (!adapter?.remove) return new Response(null, { status: 404 });
  const body = (await request.json().catch(() => ({}))) as Record<
    string,
    unknown
  >;
  return adapter.remove(body);
};
