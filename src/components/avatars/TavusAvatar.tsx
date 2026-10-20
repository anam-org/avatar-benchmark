import {
  DailyAudio,
  DailyVideo,
  useDaily,
  useDailyEvent,
  useMeetingState,
  useParticipantIds,
  useAppMessage,
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
 * Tavus condition.
 *
 * Session creation (server side, /api/tavus): a conversation on the
 * pre-created persona for this (replica, mode). The manifest records the
 * replica id; the model each
 * replica carries is in provider_configs/tavus/study_replicas.json.
 * Transport: Daily room (conversation_url).
 */

/**
 * How long after the replica's Daily participant appears to record that
 * Tavus's own readiness app-event never arrived. The kickoff does not wait
 * on it — this is a diagnostic, since phoenix-4.5 does not broadcast
 * system.pal_joined in call while phoenix-4 does.
 */
const PAL_READY_FALLBACK_MS = 4000;

/** The kickoff repeats on this interval until the avatar is heard speaking. */
const KICKOFF_RETRY_MS = 5000;
const KICKOFF_MAX_ATTEMPTS = 4;


type Props = {
  replicaId: string;
  topic?: string;
  name?: string;
  training?: boolean;
};

function SessionPage(props: Props) {
  const callObject = useDaily();
  const meetingState = useMeetingState();
  const remoteParticipantIds = useParticipantIds({ filter: "remote" });
  const conversationIdRef = useRef<string | null>(null);
  const uuidRef = useRef<string>("");
  const [agentSessionId, setAgentSessionId] = useState<string | null>(null);
  const [palReady, setPalReady] = useState(false);
  const [readyFallback, setReadyFallback] = useState(false);
  const [joined, setJoined] = useState(false);
  const [failed, setFailed] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const sendAppMessage = useAppMessage();
  const avatarSpokeRef = useRef(false);
  const attemptRef = useRef(0);
  const kickoffTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const sendKickoffRef = useRef<(via: string) => void>(() => {});

  const clearKickoffTimer = useCallback(() => {
    if (kickoffTimerRef.current) {
      clearTimeout(kickoffTimerRef.current);
      kickoffTimerRef.current = null;
    }
  }, []);

  /** Broadcast the service note, then schedule another attempt. The chain
   *  stops when the PAL is heard speaking, or after KICKOFF_MAX_ATTEMPTS. */
  const sendKickoff = useCallback(
    (via: string) => {
      const conversationId = conversationIdRef.current;
      if (!conversationId || avatarSpokeRef.current) return;
      if (attemptRef.current >= KICKOFF_MAX_ATTEMPTS) {
        logSessionEvent(uuidRef.current, "kickoff-exhausted", attemptRef.current);
        return;
      }
      attemptRef.current += 1;
      logSessionEvent(
        uuidRef.current,
        "kickoff-sent",
        `${via} attempt=${attemptRef.current}`,
      );
      sendAppMessage(
        {
          message_type: "conversation",
          event_type: "conversation.respond",
          conversation_id: conversationId,
          properties: { text: KICKOFF_NOTE },
        },
        "*",
      );
      clearKickoffTimer();
      kickoffTimerRef.current = setTimeout(
        () => sendKickoffRef.current("retry"),
        KICKOFF_RETRY_MS,
      );
    },
    [sendAppMessage, clearKickoffTimer],
  );
  sendKickoffRef.current = sendKickoff;

  const endConversation = useCallback(async () => {
    if (!conversationIdRef.current) return;
    try {
      await fetch("/api/tavus", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversationId: conversationIdRef.current }),
      });
    } catch (err) {
      console.error("Failed to end Tavus conversation:", err);
    }
    conversationIdRef.current = null;
  }, []);

  const [connectingLocal, setConnectingLocal] = useState(false);

  const endCall = useCallback(async () => {
    clearKickoffTimer();
    await leaveIfJoined(callObject);
    await endConversation();
    setAgentSessionId(null);
    setPalReady(false);
    setReadyFallback(false);
    setJoined(false);
    setConnectingLocal(false);
  }, [callObject, endConversation, clearKickoffTimer]);

  const startCall = useCallback(async (sessionUuid: string) => {
    uuidRef.current = sessionUuid;
    avatarSpokeRef.current = false;
    attemptRef.current = 0;
    clearKickoffTimer();
    setFailed(false);
    setError(null);
    setPalReady(false);
    setReadyFallback(false);
    setConnectingLocal(true);
    try {
      const response = await fetch("/api/tavus", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...props,
          sessionUuid,
          k: new URLSearchParams(window.location.search).get("k"),
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          payload?.error || `Conversation create failed (${response.status})`,
        );
      }
      conversationIdRef.current = payload.conversationId;
      logSessionEvent(sessionUuid, "mint-ok");
      await callObject?.preAuth({ url: payload.conversationUrl });
      await callObject?.join({
        url: payload.conversationUrl,
        videoSource: false,
        audioSource: true,
      });
    } catch (err) {
      setFailed(true);
      setConnectingLocal(false);
      const message =
        err instanceof Error ? err.message : "Failed to create avatar session";
      logSessionEvent(sessionUuid, "start-failed", message.slice(0, 120));
      setError(message);
    }
  }, [callObject, props]);

  useDailyEvent(
    "participant-joined",
    useCallback((ev) => {
      if (ev?.participant?.session_id) {
        setAgentSessionId(ev.participant.session_id);
        logSessionEvent(uuidRef.current, "participant-joined");
      }
    }, []),
  );

  // Tavus's readiness signal is recorded but does not gate the kickoff; its
  // speaking signal ends the retry chain.
  useDailyEvent(
    "app-message",
    useCallback(
      (ev) => {
        const data = (ev as {
          data?: { event_type?: string; properties?: { role?: string } };
        })?.data;
        const type = data?.event_type;
        if (type === "system.pal_joined" || type === "system.replica_joined") {
          logSessionEvent(uuidRef.current, "pal-ready", type);
          setPalReady(true);
        } else if (type === "conversation.started_speaking") {
          const role = data?.properties?.role;
          if (role === "user") {
            logSessionEvent(uuidRef.current, "user-speak-started");
          } else if (!avatarSpokeRef.current) {
            avatarSpokeRef.current = true;
            clearKickoffTimer();
            logSessionEvent(
              uuidRef.current,
              "avatar-speaking",
              `after ${attemptRef.current} kickoff(s)`,
            );
          }
        } else if (type === "conversation.stopped_speaking") {
          if (data?.properties?.role === "user") {
            logSessionEvent(uuidRef.current, "user-speak-ended");
          }
        } else if (type === "system.shutdown") {
          logSessionEvent(uuidRef.current, "shutdown");
        }
      },
      [clearKickoffTimer],
    ),
  );

  useDailyEvent(
    "error",
    useCallback(() => {
      setFailed(true);
      logSessionEvent(uuidRef.current, "daily-error");
      setError("An error occurred. Please try again.");
    }, []),
  );

  useEffect(() => {
    if (!meetingState) return;
    if (meetingState === "joined-meeting") setJoined(true);
    if (meetingState === "left-meeting") {
      clearKickoffTimer();
      setAgentSessionId(null);
      setPalReady(false);
      setReadyFallback(false);
      endConversation();
      setJoined(false);
      setConnectingLocal(false);
    }
  }, [meetingState, endConversation, clearKickoffTimer]);

  useEffect(() => {
    if (remoteParticipantIds.length > 0) {
      setAgentSessionId(remoteParticipantIds[0]);
    }
  }, [remoteParticipantIds]);

  useEffect(() => {
    const onUnload = () => {
      endConversation();
    };
    window.addEventListener("beforeunload", onUnload);
    return () => window.removeEventListener("beforeunload", onUnload);
  }, [endConversation]);

  // Records whether Tavus's readiness app-event arrived at all.
  useEffect(() => {
    if (!joined || !agentSessionId || palReady || readyFallback) return;
    const timer = setTimeout(() => {
      logSessionEvent(uuidRef.current, "pal-ready-timeout");
      setReadyFallback(true);
    }, PAL_READY_FALLBACK_MS);
    return () => clearTimeout(timer);
  }, [joined, agentSessionId, palReady, readyFallback]);

  // Opener kickoff: the service note prompts the model to speak its
  // [INITIATION] opener.
  //
  // Fires on Daily's participant-joined, which Tavus documents as how a
  // client knows the face has joined.
  useEffect(() => {
    if (!joined || !agentSessionId || !conversationIdRef.current) return;
    const w = window as unknown as { __tavusKickoffs?: Set<string> };
    w.__tavusKickoffs ??= new Set();
    if (w.__tavusKickoffs.has(conversationIdRef.current)) return;
    w.__tavusKickoffs.add(conversationIdRef.current);
    sendKickoff("participant-joined");
  }, [joined, agentSessionId, sendKickoff]);

  useEffect(() => clearKickoffTimer, [clearKickoffTimer]);

  const connected = joined && !!agentSessionId;
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
      session={{
        provider: "tavus",
        meta: { replicaId: props.replicaId, topic: props.topic },
      }}
    >
      {agentSessionId ? (
        <DailyVideo
          sessionId={agentSessionId}
          type="video"
          fit="contain"
          className={VIDEO_CLASSES}
        />
      ) : null}
      <DailyAudio />
    </SessionShell>
  );
}

export const TavusAvatar = (props: Props) => (
  <DailyRoot>
    <SessionPage {...props} />
  </DailyRoot>
);
