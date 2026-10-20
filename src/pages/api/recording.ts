import type { APIRoute } from "astro";
import { env as workerEnv } from "cloudflare:workers";
import { json, jsonError, verifyUrlToken } from "../../lib/api";

export const prerender = false;

const MAX_CHUNK_BYTES = 2_000_000;
const MAX_SEQ = 60;

/**
 * Session A/V recording chunks. Stored in the MANIFEST KV namespace as
 * rec:{uuid}:{seq}; reassembled offline in seq order.
 */
export const POST: APIRoute = async ({ request, url }) => {
  const uuid = url.searchParams.get("uuid") ?? "";
  const seq = Number(url.searchParams.get("seq"));
  if (!/^[a-f0-9-]{10,40}$/.test(uuid)) return jsonError(400, "uuid required");
  // Same stimulus token the page was signed with — rejects drive-by chunk
  // injection/overwrite for arbitrary session UUIDs.
  if (
    !(await verifyUrlToken(
      url.searchParams.get("k"),
      url.searchParams.get("kp") ?? "",
    ))
  ) {
    return jsonError(403, "invalid or missing token");
  }
  if (!Number.isInteger(seq) || seq < 0 || seq > MAX_SEQ) {
    return jsonError(400, "bad seq");
  }
  const body = await request.arrayBuffer();
  if (body.byteLength === 0) return jsonError(400, "empty chunk");
  if (body.byteLength > MAX_CHUNK_BYTES) return jsonError(413, "chunk too large");
  try {
    await workerEnv.MANIFEST?.put(
      `rec:${uuid}:${String(seq).padStart(3, "0")}`,
      body,
    );
  } catch (err) {
    console.error("recording chunk write failed:", err);
    return jsonError(500, "storage failed");
  }
  return json(200, { ok: true });
};
