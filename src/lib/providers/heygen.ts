import { getEnv, jsonError, missingEnv, upstreamError } from "../api";
import type { ProviderAdapter } from "./types";

/**
 * HeyGen LiveAvatar session token, FULL mode.
 *
 * The task lives in an Anam-owned platform context — one per round mode,
 * with ${topic}/${name} as dynamic variables. The contexts are exported to
 * provider_configs/heygen/.
 *
 * The contexts set no opening_text.
 */
export const heygen: ProviderAdapter = {
  idField: "avatarId",

  mint: async ({ body, id, topic, name, mode }) => {
    const HEYGEN_API_KEY = getEnv("HEYGEN_API_KEY");
    if (!HEYGEN_API_KEY) return missingEnv("HEYGEN_API_KEY");
    const contextId =
      mode === "practice"
        ? getEnv("LIVEAVATAR_CONTEXT_ID_PRACTICE")
        : getEnv("LIVEAVATAR_CONTEXT_ID_MAIN");
    if (!contextId) {
      return missingEnv(
        mode === "practice"
          ? "LIVEAVATAR_CONTEXT_ID_PRACTICE"
          : "LIVEAVATAR_CONTEXT_ID_MAIN",
      );
    }
    const voiceId = body.voiceId as string | undefined;
    if (!voiceId) return jsonError(400, "voiceId is required");

    const res = await fetch("https://api.liveavatar.com/v1/sessions/token", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-API-KEY": HEYGEN_API_KEY,
      },
      body: JSON.stringify({
        mode: "FULL",
        avatar_id: id,
        interactivity_type: "CONVERSATIONAL",
        avatar_persona: {
          voice_id: voiceId,
          context_id: contextId,
          language: "en",
        },
        dynamic_variables: { topic, name },
      }),
    });
    if (!res.ok) return upstreamError("LiveAvatar token failed", res);
    const data = (await res.json()) as { data?: { session_token?: string } };
    const sessionToken = data.data?.session_token;
    if (!sessionToken) return jsonError(502, "No session_token in response");

    return {
      response: { sessionToken },
      manifest: { avatarId: id, voiceId, contextId, tokenIssued: true },
    };
  },
};
