import {
  AccessToken,
  RoomAgentDispatch,
  RoomConfiguration,
} from "livekit-server-sdk";
import lkConfig from "../../config/livekit.json";
import { getEnv, jsonError, missingEnv } from "../api";
import { avatarModelFor, slotsFor } from "../arms";
import type { ProviderAdapter } from "./types";

/**
 * LiveKit study: mint a room token whose agent dispatch metadata tells the
 * shared pipeline which arm's avatar to attach. Prompt and opener are
 * NOT sent.
 *
 * `provider` is the arm key. It is the same string in the route, in the signed
 * token and in the dispatch metadata, and the agent resolves the cast and the
 * avatar model from it.
 *
 * Voice: the first 32 bits of sha256(sessionUuid) mod pool size,
 * gender-matched to the slot. Pools: see config/livekit.json.
 */
export const livekit: ProviderAdapter = {
  idField: "provider",
  tokenPayload: (body) =>
    [
      "livekit",
      body.provider,
      body.slot,
      body.topic,
      body.training ? "practice" : "main",
    ].join(":"),

  mint: async ({ body, topic, name, mode, sessionUuid }) => {
    const LIVEKIT_URL = getEnv("LIVEKIT_URL");
    const LIVEKIT_API_KEY = getEnv("LIVEKIT_API_KEY");
    const LIVEKIT_API_SECRET = getEnv("LIVEKIT_API_SECRET");
    if (!LIVEKIT_URL) return missingEnv("LIVEKIT_URL");
    if (!LIVEKIT_API_KEY) return missingEnv("LIVEKIT_API_KEY");
    if (!LIVEKIT_API_SECRET) return missingEnv("LIVEKIT_API_SECRET");

    const provider = body.provider as string;
    const slot = body.slot as string | undefined;
    const slots = slotsFor(provider);
    if (!slots) return jsonError(400, "unknown provider");
    if (!slot || !slots[slot]) {
      return jsonError(400, "unknown slot");
    }
    // Empty for every arm but an Anam one pinning a model.
    const avatarModel = avatarModelFor(provider);

    const pool = slot.startsWith("f")
      ? lkConfig.pipeline.tts.voicePoolFemale
      : lkConfig.pipeline.tts.voicePoolMale;
    const digest = await crypto.subtle.digest(
      "SHA-256",
      new TextEncoder().encode(sessionUuid),
    );
    const voice = pool[new DataView(digest).getUint32(0) % pool.length];

    const roomName = `bench-${sessionUuid}`;
    const metadata = JSON.stringify({
      provider,
      slot,
      topic,
      name,
      mode,
      sessionUuid,
      voiceId: voice.id,
      avatarModel,
    });

    const token = new AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET, {
      identity: `rater-${sessionUuid}`,
      ttl: "15m",
    });
    token.addGrant({ roomJoin: true, room: roomName });
    token.roomConfig = new RoomConfiguration({
      agents: [
        new RoomAgentDispatch({ agentName: lkConfig.agentName, metadata }),
      ],
    });

    return {
      response: { url: LIVEKIT_URL, token: await token.toJwt() },
      manifest: {
        provider,
        slot,
        roomName,
        voiceId: voice.id,
        voiceName: voice.name,
        avatarModel: avatarModel || null,
        pipeline: lkConfig.pipeline,
      },
    };
  },
};
