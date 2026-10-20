import {
  AgentEventsEnum,
  LiveAvatarSession,
  SessionEvent,
  SessionState,
} from "@heygen/liveavatar-web-sdk";
import { useCallback, useEffect, useRef, useState } from "react";
import { KICKOFF_NOTE } from "../../lib/kickoff";
import {
  SessionShell,
  VIDEO_CLASSES,
  type SessionStatus,
} from "../session/SessionShell";
import { logSessionEvent } from "../session/sessionLog";

/**
 * HeyGen condition (LiveAvatar).
 *
 * Session creation (server side, /api/heygen): LiveAvatar session token in
 * FULL mode. The 20 Questions task lives in LiveAvatar *contexts* (one per
 * round, dashboard-configured, referenced by LIVEAVATAR_CONTEXT_ID_MAIN and
 * LIVEAVATAR_CONTEXT_ID_PRACTICE); the topic is injected via
 * dynamic_variables.
 *
 * The contexts carry no opening_text.
 */

/**
 * The kickoff is repeated until the avatar is heard speaking.
 *
 * The first attempt waits for SessionState.CONNECTED.
 */
const KICKOFF_RETRY_MS = 5000;
const KICKOFF_MAX_ATTEMPTS = 4;

type Props = {
  avatarId: string;
  voiceId: string;
  topic: string;
  name?: string;
  training?: boolean;
};

export const HeyGenAvatar = ({
  avatarId,
  voiceId,
  topic,
  name,
  training,
}: Props) => {
  const videoRef = useRef<HTMLVideoElement>(null);
  const sessionRef = useRef<LiveAvatarSession | null>(null);
  const uuidRef = useRef<string>("");
  const avatarSpokeRef = useRef(false);
  const attemptRef = useRef(0);
  const kickoffTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const sendKickoffRef = useRef<(via: string) => void>(() => {});
  const [status, setStatus] = useState<SessionStatus>("idle");
  const [error, setError] = useState<string | null>(null);

  const clearKickoffTimer = useCallback(() => {
    if (kickoffTimerRef.current) {
      clearTimeout(kickoffTimerRef.current);
      kickoffTimerRef.current = null;
    }
  }, []);

  /** Trigger the model's turn, then schedule another attempt. `message()`
   *  sends `avatar.speak_response`. The chain stops when the avatar is
   *  heard speaking, or after KICKOFF_MAX_ATTEMPTS. */
  const sendKickoff = useCallback(
    (via: string) => {
      const session = sessionRef.current;
      if (!session || avatarSpokeRef.current) return;
      if (attemptRef.current >= KICKOFF_MAX_ATTEMPTS) {
        logSessionEvent(uuidRef.current, "kickoff-exhausted", attemptRef.current);
        return;
      }
      try {
        session.message(KICKOFF_NOTE);
        attemptRef.current += 1;
        logSessionEvent(
          uuidRef.current,
          "kickoff-sent",
          `${via} attempt=${attemptRef.current}`,
        );
      } catch (err) {
        // The note never left the browser, so it does not consume one of
        // the attempts; only the retry cadence applies.
        logSessionEvent(
          uuidRef.current,
          "kickoff-failed",
          err instanceof Error ? err.message.slice(0, 120) : "unknown",
        );
      }
      clearKickoffTimer();
      kickoffTimerRef.current = setTimeout(
        () => sendKickoffRef.current("retry"),
        KICKOFF_RETRY_MS,
      );
    },
    [clearKickoffTimer],
  );
  sendKickoffRef.current = sendKickoff;

  const endCall = useCallback(() => {
    clearKickoffTimer();
    try {
      sessionRef.current?.stop();
    } catch {
      // Ignore cleanup errors.
    }
    sessionRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    setStatus("idle");
  }, [clearKickoffTimer]);

  const startCall = useCallback(async (sessionUuid: string) => {
    uuidRef.current = sessionUuid;
    avatarSpokeRef.current = false;
    attemptRef.current = 0;
    clearKickoffTimer();
    setError(null);
    setStatus("connecting");
    try {
      const res = await fetch("/api/heygen", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          avatarId,
          voiceId,
          topic,
          name,
          training,
          sessionUuid,
          k: new URLSearchParams(window.location.search).get("k"),
        }),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error || "Session creation failed");
      logSessionEvent(sessionUuid, "mint-ok");

      const session = new LiveAvatarSession(json.sessionToken, {
        voiceChat: true,
      });
      sessionRef.current = session;

      session.on(SessionEvent.SESSION_STREAM_READY, async () => {
        logSessionEvent(sessionUuid, "stream-ready");
        if (videoRef.current) {
          session.attach(videoRef.current);
          videoRef.current.muted = false;
        }
        setStatus("connected");
        try {
          await session.voiceChat.start();
          logSessionEvent(sessionUuid, "voice-chat-started");
        } catch (err) {
          console.error("Failed to start voice chat:", err);
          logSessionEvent(
            sessionUuid,
            "voice-chat-failed",
            err instanceof Error ? err.message.slice(0, 120) : "unknown",
          );
        }
      });

      // The kickoff waits for this, not for the stream: the SDK rejects a
      // command event until the session reaches CONNECTED.
      session.on(SessionEvent.SESSION_STATE_CHANGED, (state) => {
        logSessionEvent(sessionUuid, "session-state", String(state));
        if (state === SessionState.CONNECTED) sendKickoff("connected");
      });

      // Turn telemetry. avatar.speak_started additionally ends the kickoff
      // sequence.
      session.on(AgentEventsEnum.AVATAR_SPEAK_STARTED, () => {
        logSessionEvent(sessionUuid, "avatar-speak-started");
        if (!avatarSpokeRef.current) {
          avatarSpokeRef.current = true;
          clearKickoffTimer();
          logSessionEvent(
            sessionUuid,
            "avatar-speaking",
            `after ${attemptRef.current} kickoff(s)`,
          );
        }
      });
      session.on(AgentEventsEnum.AVATAR_SPEAK_ENDED, () => {
        logSessionEvent(sessionUuid, "avatar-speak-ended");
      });
      session.on(AgentEventsEnum.USER_SPEAK_STARTED, () => {
        logSessionEvent(sessionUuid, "user-speak-started");
      });
      session.on(AgentEventsEnum.USER_SPEAK_ENDED, () => {
        logSessionEvent(sessionUuid, "user-speak-ended");
      });
      session.on(AgentEventsEnum.SESSION_STOPPED, (ev) => {
        logSessionEvent(sessionUuid, "session-stopped", ev?.stop_reason ?? null);
      });

      session.on(SessionEvent.SESSION_DISCONNECTED, (reason) => {
        logSessionEvent(sessionUuid, "disconnected", reason ?? null);
        clearKickoffTimer();
        sessionRef.current = null;
        if (videoRef.current) videoRef.current.srcObject = null;
        setStatus("idle");
      });

      await session.start();
    } catch (err) {
      console.error(err);
      const message =
        err instanceof Error ? err.message : "Failed to start HeyGen session";
      logSessionEvent(sessionUuid, "start-failed", message.slice(0, 120));
      clearKickoffTimer();
      setError(message);
      setStatus("error");
      sessionRef.current = null;
    }
  }, [avatarId, voiceId, topic, name, training, sendKickoff, clearKickoffTimer]);

  useEffect(() => {
    return () => {
      if (kickoffTimerRef.current) clearTimeout(kickoffTimerRef.current);
      try {
        sessionRef.current?.stop();
      } catch {
        // Ignore cleanup errors.
      }
      sessionRef.current = null;
    };
  }, []);

  return (
    <SessionShell
      status={status}
      error={error}
      onStart={startCall}
      onEnd={endCall}
      session={{ provider: "heygen", meta: { avatarId, voiceId, topic } }}
    >
      <video
        ref={videoRef}
        autoPlay
        playsInline
        className={VIDEO_CLASSES}
      />
    </SessionShell>
  );
};
