import providersConfig from "../../config/providers.json";
import { getEnv, jsonError, missingEnv, upstreamError } from "../api";
import type { ProviderAdapter } from "./types";

const TAVUS_HOST = "https://tavusapi.com";

const authHeaders = (key: string) => ({
  "Content-Type": "application/json",
  "x-api-key": key,
});

/**
 * Tavus conversation on a pre-created persona.
 *
 * The task is the persona's system_prompt (sent once — the conversation's
 * conversational_context is NOT also set). The opener is an LLM
 * turn triggered by the client's conversation.respond kickoff (see
 * TavusAvatar). The manifest records the replica id;
 * provider_configs/tavus/study_replicas.json records the model each fielded
 * replica carries.
 *
 * Personas are created by scripts/create_tavus_personas.py and referenced
 * here by (replica, mode). The prompt the route composes is not sent from
 * here; what each persona carries is checked against src/prompts by
 * scripts/check_tavus_persona_prompts.sh.
 */
export const tavus: ProviderAdapter = {
  idField: "replicaId",

  mint: async ({ id, topic, mode, capSeconds }) => {
    const TAVUS_API_KEY = getEnv("TAVUS_API_KEY");
    if (!TAVUS_API_KEY) return missingEnv("TAVUS_API_KEY");

    const personas: Record<string, Record<string, string>> =
      providersConfig.tavus?.personas ?? {};
    const persona_id = personas[id]?.[mode];
    // A missing persona fails the mint.
    if (!persona_id) {
      return jsonError(500, `No pre-created Tavus persona for replica ${id} (${mode})`);
    }

    const convRes = await fetch(`${TAVUS_HOST}/v2/conversations`, {
      method: "POST",
      headers: authHeaders(TAVUS_API_KEY),
      body: JSON.stringify({
        persona_id,
        conversation_name: `Conversation - ${topic}`,
        properties: { max_call_duration: capSeconds },
      }),
    });
    if (!convRes.ok) {
      return upstreamError("Failed to create conversation", convRes);
    }
    const { conversation_id, conversation_url } = await convRes.json();

    return {
      response: {
        conversationId: conversation_id,
        conversationUrl: conversation_url,
      },
      manifest: {
        replicaId: id,
        personaId: persona_id,
        conversationId: conversation_id,
        maxCallDuration: capSeconds,
      },
    };
  },

  // Routine call cleanup (end-conversation): POST /end when the rater
  // leaves.
  remove: async (body) => {
    const TAVUS_API_KEY = getEnv("TAVUS_API_KEY");
    if (!TAVUS_API_KEY) return new Response(null, { status: 500 });
    const conversationId = body.conversationId as string | undefined;
    // Must be an opaque Tavus id and nothing else. It is interpolated into a
    // path, and URL parsing resolves dot-segments, so an unvalidated value
    // steers the authenticated request to another endpoint.
    if (!conversationId || !/^c[a-f0-9]{15}$/.test(conversationId)) {
      return new Response(null, { status: 400 });
    }
    await fetch(`${TAVUS_HOST}/v2/conversations/${conversationId}/end`, {
      method: "POST",
      headers: { "x-api-key": TAVUS_API_KEY },
    });
    return new Response(null, { status: 204 });
  },
};
