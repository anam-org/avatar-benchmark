import providersConfig from "../../config/providers.json";
import { getEnv, jsonError, missingEnv, upstreamError } from "../api";
import { bindPayload, corePayload } from "../token";
import type { ProviderAdapter } from "./types";

/** Avatar models an Anam arm may pin, from config/providers.json. */
const ARM_MODELS: ReadonlySet<string> = new Set(
  Object.values(providersConfig.anam.arms ?? {}).map((a) => a.avatarModel),
);

/**
 * Anam session token. The LLM is read from config/providers.json and sent on
 * every mint.
 *
 * An arm may pin an avatar model via `avatarModel`. It is bound into the
 * stimulus token and checked against the arms config.
 */
export const anam: ProviderAdapter = {
  idField: "avatarId",

  tokenPayload: (body, id) =>
    bindPayload(
      corePayload({
        provider: "anam",
        id,
        topic: (body.topic as string) ?? "",
        mode: body.training ? "practice" : "main",
        voice: (body.voiceId as string) ?? "",
        model: (body.model as string) ?? "",
      }),
      (body.avatarModel as string) ?? "",
    ),

  mint: async ({ body, id, name, systemPrompt, capSeconds }) => {
    const ANAM_API_KEY = getEnv("ANAM_API_KEY");
    if (!ANAM_API_KEY) return missingEnv("ANAM_API_KEY");
    const voiceId = body.voiceId as string | undefined;
    const model = body.model as string | undefined;
    if (!voiceId || !model) return jsonError(400, "voiceId and model are required");
    const avatarModel = (body.avatarModel as string | undefined) || "";
    if (avatarModel && !ARM_MODELS.has(avatarModel)) {
      return jsonError(400, "unknown avatarModel");
    }

    const res = await fetch("https://api.anam.ai/v1/auth/session-token", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${ANAM_API_KEY}`,
      },
      body: JSON.stringify({
        personaConfig: {
          name,
          avatarId: id,
          avatarModel: avatarModel || model,
          voiceId,
          llmId: providersConfig.anam.llmId,
          maxSessionLengthSeconds: capSeconds,
          systemPrompt,
        },
      }),
    });
    if (!res.ok) return upstreamError("Anam session-token failed", res);
    const { sessionToken } = (await res.json()) as { sessionToken?: string };
    if (!sessionToken) return jsonError(502, "No sessionToken in response");

    return {
      response: { sessionToken },
      manifest: {
        avatarId: id,
        voiceId,
        model,
        // Null where the arm pins no model.
        avatarModel: avatarModel || null,
        llmId: providersConfig.anam.llmId,
        capSeconds,
        tokenIssued: true,
      },
    };
  },
};
