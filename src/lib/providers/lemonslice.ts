import { getEnv, jsonError, missingEnv, upstreamError } from "../api";
import type { ProviderAdapter } from "./types";

/**
 * LemonSlice hosted-pipeline room on a dashboard-configured agent.
 *
 * The room call takes only the agent_id — avatar, voice, LLM, model tier and
 * the task system prompt (topic + name baked per agent) all live in the
 * dashboard agent.
 */
export const lemonslice: ProviderAdapter = {
  idField: "avatarId",

  mint: async ({ id }) => {
    const LEMONSLICE_API_KEY = getEnv("LEMONSLICE_API_KEY");
    if (!LEMONSLICE_API_KEY) return missingEnv("LEMONSLICE_API_KEY");

    const upstream = await fetch("https://lemonslice.com/api/liveai/rooms", {
      method: "POST",
      headers: {
        "X-API-Key": LEMONSLICE_API_KEY,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ agent_id: id }),
    });
    if (!upstream.ok) return upstreamError("Failed to create room", upstream);
    const data = (await upstream.json()) as {
      room_url?: string;
      token?: string;
      session_id?: string;
    };
    if (!data.room_url || !data.token) {
      return jsonError(502, "LemonSlice response missing room_url/token");
    }

    return {
      response: { roomUrl: data.room_url, token: data.token },
      manifest: { agentId: id, sessionId: data.session_id },
    };
  },
};
