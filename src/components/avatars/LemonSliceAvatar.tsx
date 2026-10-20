import {
  DailyAudio,
  DailyVideo,
  useAppMessage,
  useDaily,
  useDailyEvent,
  useMeetingState,
  useParticipantIds,
} from "@daily-co/daily-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { KICKOFF_NOTE } from "../../lib/kickoff";
import { DailyRoot, leaveIfJoined } from "../session/DailyRoot";
import {
  SessionShell,
  VIDEO_CLASSES,
  type SessionStatus,
} from "../session/SessionShell";
import { logSessionEvent } from "../session/sessionLog";

/**
 * LemonSlice condition.
 *
 * Session creation (server side, /api/lemonslice): hosted-pipeline room on a
 * dashboard-configured agent (avatar/voice/LLM/tier live in the agent). The
 * 20 Questions rules AND the topic are baked into each agent's dashboard
 * SYSTEM prompt; a dedicated practice agent covers training rounds.
 * Transport: Daily room (room_url).
 */

type Props = {
  avatarId: string;
  topic?: string;
  name?: string;
  training?: boolean;
};

function SessionPage({ avatarId, topic, name, training }: Props) {
  const callObject = useDaily();
  const sendAppMessage = useAppMessage();
  const meetingState = useMeetingState();
  const remoteParticipantIds = useParticipantIds({ filter: "remote" });
  const userEndedRef = useRef(false);
  const uuidRef = useRef<string>("");
  const [hasAgentJoinedRoom, setHasAgentJoinedRoom] = useState(false);
  const [isAgentReady, setIsAgentReady] = useState(false);
  const [agentSessionId, setAgentSessionId] = useState<string | null>(null);
  const [joined, setJoined] = useState(false);
  const [connectingLocal, setConnectingLocal] = useState(false);
  const [failed, setFailed] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const resetSessionState = useCallback(() => {
    setIsAgentReady(false);
    setHasAgentJoinedRoom(false);
    setAgentSessionId(null);
    setError(null);
  }, []);

  const endCall = useCallback(async () => {
    userEndedRef.current = true;
    if (callObject) {
      try {
        const state = callObject.meetingState();
        if (state === "joined-meeting" || state === "joining-meeting") {
          sendAppMessage({ event: "force-end" }, "*");
        }
      } catch {
        // Ignore cleanup errors.
      }
    }
    await leaveIfJoined(callObject);
    resetSessionState();
    setJoined(false);
    setConnectingLocal(false);
  }, [callObject, sendAppMessage, resetSessionState]);

  const startCall = useCallback(async (sessionUuid: string) => {
    uuidRef.current = sessionUuid;
    userEndedRef.current = false;
    resetSessionState();
    setFailed(false);
    setConnectingLocal(true);
    try {
      const response = await fetch("/api/lemonslice", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          avatarId,
          topic: new URLSearchParams(window.location.search).get("topic"),
          name: new URLSearchParams(window.location.search).get("name"),
          training:
            new URLSearchParams(window.location.search).get("training") ===
            "true",
          sessionUuid,
          k: new URLSearchParams(window.location.search).get("k"),
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          payload?.error || `Room create failed (${response.status})`,
        );
      }
      const roomUrl = payload?.roomUrl;
      const roomToken = payload?.token;
      if (!roomUrl) throw new Error("Room create response missing roomUrl");
      logSessionEvent(sessionUuid, "mint-ok");
      // Join with the user token from room creation.
      await callObject?.join({
        url: roomUrl,
        token: roomToken,
        audioSource: true,
        videoSource: false,
        startAudioOff: false,
      });
      setHasAgentJoinedRoom(true);
    } catch (err) {
      setFailed(true);
      setConnectingLocal(false);
      const message =
        err instanceof Error ? err.message : "Failed to create avatar session";
      logSessionEvent(sessionUuid, "start-failed", message.slice(0, 120));
      setError(message);
    }
  }, [callObject, avatarId, resetSessionState]);

  useDailyEvent(
    "participant-left",
    useCallback(() => {
      if (userEndedRef.current) return;
      setFailed(true);
      setError("Agent left the call");
    }, []),
  );

  useDailyEvent(
    "error",
    useCallback(() => {
      setFailed(true);
      setError("An error occurred. Please try again.");
    }, []),
  );

  useDailyEvent(
    "app-message",
    useCallback((ev) => {
      const type = (ev as { data?: { type?: string; error?: string } })?.data
        ?.type;
      if (type) logSessionEvent(uuidRef.current, `ls:${type}`);
      if (type === "bot_ready") setIsAgentReady(true);
      if (type === "idle_timeout") {
        setFailed(true);
        setError("Agent has hit idle timeout");
      }
      if (type === "daily_error") {
        setFailed(true);
        setError(
          `Pipeline error: ${(ev as { data?: { error?: string } })?.data?.error ?? "unknown"}`,
        );
      }
      if (type === "video_generation_error") {
        setFailed(true);
        setError("Video generation error");
      }
    }, []),
  );

  useEffect(() => {
    if (!meetingState) return;
    if (meetingState === "joined-meeting") setJoined(true);
    if (meetingState === "left-meeting") {
      resetSessionState();
      setJoined(false);
      setConnectingLocal(false);
    }
    if (meetingState === "error") {
      setFailed(true);
      setError("Daily call error");
    }
  }, [meetingState, resetSessionState]);

  // Once it reports ready we send ONE service-note kickoff over the
  // platform's chat channel to trigger its opening turn. Send-once guard is
  // window-scoped (keyed by agent session).
  useEffect(() => {
    if (!isAgentReady || !agentSessionId) return;
    const w = window as unknown as { __lsKickoffs?: Set<string> };
    w.__lsKickoffs ??= new Set();
    if (w.__lsKickoffs.has(agentSessionId)) return;
    w.__lsKickoffs.add(agentSessionId);
    logSessionEvent(uuidRef.current, "kickoff-sent", "bot-ready");
    sendAppMessage(
      { event: "chat-msg", message: KICKOFF_NOTE, name: "User" },
      "*",
    );
  }, [isAgentReady, agentSessionId, sendAppMessage]);

  useEffect(() => {
    if (!hasAgentJoinedRoom || remoteParticipantIds.length !== 1) return;
    setAgentSessionId(remoteParticipantIds[0]);
  }, [hasAgentJoinedRoom, remoteParticipantIds]);

  useEffect(() => {
    const onUnload = () => {
      userEndedRef.current = true;
      leaveIfJoined(callObject);
    };
    window.addEventListener("beforeunload", onUnload);
    return () => window.removeEventListener("beforeunload", onUnload);
  }, [callObject]);

  const connected =
    joined && hasAgentJoinedRoom && isAgentReady && !!agentSessionId;
  const status: SessionStatus = failed
    ? "error"
    : connected
      ? "connected"
      : connectingLocal || joined
        ? "connecting"
        : "idle";

  return (
    <SessionShell
      status={status}
      error={error}
      onStart={startCall}
      onEnd={endCall}
      session={{ provider: "lemonslice", meta: { agentId: avatarId, topic } }}
    >
      {connected ? (
        <DailyVideo
          sessionId={agentSessionId!}
          type="video"
          fit="contain"
          className={VIDEO_CLASSES}
        />
      ) : null}
      <DailyAudio />
    </SessionShell>
  );
}

export const LemonSliceAvatar = (props: Props) => (
  <DailyRoot>
    <SessionPage {...props} />
  </DailyRoot>
);
