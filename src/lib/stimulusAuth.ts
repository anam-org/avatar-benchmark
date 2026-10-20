import { bindPayload, corePayload } from "./token";

/**
 * The stimulus token payload, rebuilt from the page URL.
 *
 * The session page holds a signed URL but not the payload that signature
 * covers, so the evidence routes (/api/manifest, /api/recording) are given
 * the token AND the payload string, and verify one against the other. That
 * makes this function the mirror of the mint route's payload construction
 * in src/pages/api/[provider].ts: whatever the dataset builder signed, this
 * has to reproduce from the query string alone.
 *
 * The two are checked against each other by tools/check_evidence_payload.sh,
 * which signs real builder output and verifies it against what this derives.
 */
export function stimulusPayload(
  pathname: string,
  params: URLSearchParams,
): string {
  const path = pathname.split("/");
  const provider = path[2] ?? "";
  const mode = params.get("training") === "true" ? "practice" : "main";
  // Bound whenever present, exactly as the mint route binds it: the
  // avatar model an arm pins.
  const avatarModel = params.get("avatarModel") ?? "";

  if (path[1] === "livekit") {
    // The LiveKit adapter substitutes its own core (providers/livekit.ts
    // tokenPayload): the avatar is addressed by slot, not by id.
    return [
      "livekit",
      provider,
      params.get("slot") ?? "",
      params.get("topic") ?? "",
      mode,
    ].join(":");
  }

  return bindPayload(
    corePayload({
      provider,
      id: params.get("avatarId") ?? params.get("replicaId") ?? "",
      topic: params.get("topic") ?? "",
      mode,
      voice: params.get("voiceId") ?? "",
      model: params.get("model") ?? "",
    }),
    avatarModel,
  );
}
