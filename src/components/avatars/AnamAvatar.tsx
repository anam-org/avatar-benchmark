import { AnamEvent, createClient } from "@anam-ai/js-sdk";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  SessionShell,
  VIDEO_CLASSES,
  type SessionStatus,
} from "../session/SessionShell";
import { logSessionEvent } from "../session/sessionLog";

/**
 * Anam condition.
 *
 * Session creation (server side, /api/anam): Anam session-token API with the
 * study avatar/voice, the LLM named in config/providers.json, and the
 * 20 Questions system prompt. Transport: Anam JS SDK (WebRTC) into a <video>.
 */

type AnamClient = ReturnType<typeof createClient>;

type Props = {
  avatarId: string;
  voiceId: string;
  model: "cara-4";
  /** Avatar model pinned by this arm; omitted runs the account default. */
  avatarModel?: string;
  topic?: string;
  name?: string;
  training?: boolean;
};

export const AnamAvatar = ({
  avatarId,
  voiceId,
  model,
  avatarModel,
  topic,
  name,
  training,
}: Props) => {
  const videoRef = useRef<HTMLVideoElement>(null);
  const clientRef = useRef<AnamClient | null>(null);
  const [status, setStatus] = useState<SessionStatus>("idle");
  const [error, setError] = useState<string | null>(null);

  const endCall = useCallback(() => {
    try {
      clientRef.current?.stopStreaming?.();
    } catch {
      // Ignore cleanup errors.
    }
    clientRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    setStatus("idle");
  }, []);

  const startCall = useCallback(async (sessionUuid: string) => {
    setError(null);
    setStatus("connecting");
    try {
      logSessionEvent(sessionUuid, "mint-request");
      const res = await fetch("/api/anam", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          avatarId,
          voiceId,
          model,
          avatarModel,
          name,
          topic,
          training,
                  sessionUuid,
          k: new URLSearchParams(window.location.search).get("k"),
        }),
      });
      if (!res.ok) {
        const payload = await res.json().catch(() => ({}));
        logSessionEvent(sessionUuid, "mint-failed", res.status);
        throw new Error(
          payload?.error || `Anam session-token request failed (${res.status})`,
        );
      }
      const { sessionToken } = await res.json();
      logSessionEvent(sessionUuid, "mint-ok");

      const client = createClient(sessionToken);
      clientRef.current = client;

      client.addListener(AnamEvent.CONNECTION_ESTABLISHED, () => {
        logSessionEvent(sessionUuid, "connection-established");
      });
      client.addListener(AnamEvent.DATA_CHANNEL_OPEN, () => {
        logSessionEvent(sessionUuid, "data-channel-open");
      });
      client.addListener(AnamEvent.SESSION_READY, () => {
        logSessionEvent(sessionUuid, "session-ready");
      });
      client.addListener(AnamEvent.VIDEO_STREAM_STARTED, () => {
        logSessionEvent(sessionUuid, "video-stream-started");
      });
      client.addListener(AnamEvent.AUDIO_STREAM_STARTED, () => {
        logSessionEvent(sessionUuid, "audio-stream-started");
      });
      client.addListener(AnamEvent.SERVER_WARNING, (warning) => {
        logSessionEvent(
          sessionUuid,
          "server-warning",
          typeof warning === "string" ? warning.slice(0, 120) : null,
        );
      });
      client.addListener(AnamEvent.MIC_PERMISSION_DENIED, () => {
        logSessionEvent(sessionUuid, "mic-permission-denied");
      });
      client.addListener(AnamEvent.VIDEO_PLAY_STARTED, () => {
        logSessionEvent(sessionUuid, "video-play-started");
        setStatus("connected");
      });
      client.addListener(AnamEvent.CONNECTION_CLOSED, (reason, details) => {
        logSessionEvent(
          sessionUuid,
          "connection-closed",
          [reason, details].filter(Boolean).join(" ").slice(0, 120) || null,
        );
        clientRef.current = null;
        if (videoRef.current) videoRef.current.srcObject = null;
        setStatus("idle");
      });

      if (videoRef.current) {
        logSessionEvent(sessionUuid, "stream-requested");
        await client.streamToVideoElement(videoRef.current.id);
      }
    } catch (err) {
      console.error("Anam error:", err);
      const code = (err as { code?: string })?.code;
      const statusCode = (err as { statusCode?: number })?.statusCode;
      const message =
        err instanceof Error ? err.message : "Failed to start Anam session";
      logSessionEvent(
        sessionUuid,
        "start-failed",
        [code, statusCode, message].filter(Boolean).join(" ").slice(0, 160),
      );
      setError(message);
      setStatus("error");
      clientRef.current = null;
    }
  }, [avatarId, voiceId, model, avatarModel, name, topic, training]);

  useEffect(() => {
    return () => {
      try {
        clientRef.current?.stopStreaming?.();
      } catch {
        // Ignore cleanup errors.
      }
      clientRef.current = null;
    };
  }, []);

  return (
    <SessionShell
      status={status}
      error={error}
      onStart={startCall}
      onEnd={endCall}
      session={{
        provider: "anam",
        meta: { avatarId, voiceId, model, avatarModel, topic },
      }}
    >
      <video
        ref={videoRef}
        id="anamVideoElement"
        autoPlay
        playsInline
        className={VIDEO_CLASSES}
      />
    </SessionShell>
  );
};
