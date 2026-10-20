import type { APIRoute } from "astro";
import { json, jsonError, recordManifest, verifyUrlToken } from "../../lib/api";

export const prerender = false;

/**
 * Client-side evidence capture. The SessionShell posts one record per
 * session: which provider/avatar ran, what video resolution/framerate
 * was actually DELIVERED to this rater (sampled over the session), the
 * rater's display context (devicePixelRatio, viewport), the connection
 * milestones the session reached, and the rating platform's presentation
 * id. Stored in the MANIFEST KV namespace.
 */
export const POST: APIRoute = async ({ request }) => {
  const body = await request.text();
  // Sized for a long session's event log plus its recorder chunk log.
  if (body.length > 65_536) return jsonError(413, "manifest too large");
  let record: Record<string, unknown>;
  try {
    record = JSON.parse(body);
  } catch {
    return jsonError(400, "invalid JSON");
  }
  const uuid = typeof record.sessionUuid === "string" ? record.sessionUuid : "";
  if (!/^[a-f0-9-]{10,40}$/.test(uuid)) {
    return jsonError(400, "sessionUuid required");
  }
  // Evidence writes carry the page's stimulus token + its payload string;
  // rejects drive-by injection of manifests for arbitrary session UUIDs.
  if (!(await verifyUrlToken(record.k, record.kPayload as string))) {
    return jsonError(403, "invalid or missing token");
  }
  delete record.k;
  delete record.kPayload;
  await recordManifest("client", uuid, record);
  return json(200, { ok: true });
};
