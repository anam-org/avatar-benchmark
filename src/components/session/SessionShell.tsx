import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import experimentConfig from "../../config/experiment.json";
import { stimulusPayload } from "../../lib/stimulusAuth";
import { probeConnectivity, type ConnectivityProbe } from "./connectivityProbe";
import {
  drainSessionLog,
  logSessionEvent,
  startSessionLog,
  type SessionLogEntry,
} from "./sessionLog";
import {
  startSessionRecorder,
  type RecorderStats,
  type ChunkLogEntry,
} from "./sessionRecorder";

/**
 * The presentation layer.
 *
 * - The video area fills the window.
 * - Fit is CONTAIN (see VIDEO_CLASSES).
 *   Letterbox/pillarbox areas are filled with an ambient blur of the same
 *   video rather than black.
 * - A visible countdown (sessionCapSeconds from config/experiment.json)
 *   runs during the session, and the session ends when it reaches zero.
 *
 * Evidence capture: the shell generates a session UUID (passed to the
 * provider's start call so server-side records join up), samples the
 * DELIVERED video (videoWidth/Height, track settings) plus the rater's
 * display context, and beacons one manifest record to /api/manifest when
 * the session ends.
 */

export type SessionStatus = "idle" | "connecting" | "connected" | "error";

export const VIDEO_CLASSES = "h-full w-full object-contain";

export const SESSION_CAP_SECONDS: number = experimentConfig.sessionCapSeconds;

type Props = {
  status: SessionStatus;
  error: string | null;
  onStart: (sessionUuid: string) => void;
  onEnd: () => void;
  /** Condition identity for the evidence manifest. */
  session: { provider: string; meta: Record<string, unknown> };
  /** The provider's video node (and any hidden audio elements). */
  children: ReactNode;
};

const formatTime = (s: number) =>
  `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

type DeliverySample = {
  t: number;
  w: number;
  h: number;
  fps?: number;
  displayW?: number;
  displayH?: number;
  droppedFrames?: number;
  totalFrames?: number;
};

const pageParams = () =>
  typeof window === "undefined"
    ? new URLSearchParams()
    : new URLSearchParams(window.location.search);

/**
 * The stimulus token + the payload it signs, reconstructed from the page
 * URL exactly as the mint route builds it (see lib/stimulusAuth.ts).
 */
const stimulusAuth = (): { k: string; payload: string } => ({
  k: pageParams().get("k") ?? "",
  payload:
    typeof window === "undefined"
      ? ""
      : stimulusPayload(window.location.pathname, pageParams()),
});


/**
 * The rating platform's presentation id, minted by the dataset wrapper and
 * handed to this page. Empty when the page is opened directly.
 */
const presentationId = (): string => pageParams().get("pid") ?? "";

/**
 * Report session identity back to the wrapper. Best-effort and one-way.
 */
const postToHost = (message: Record<string, unknown>): void => {
  try {
    window.parent?.postMessage(
      { source: "anam-benchmark", ...message },
      "*",
    );
  } catch {
    // Ignore.
  }
};

export const SessionShell = ({
  status,
  error,
  onStart,
  onEnd,
  session,
  children,
}: Props) => {
  const [secondsLeft, setSecondsLeft] = useState(SESSION_CAP_SECONDS);
  // One session per PAGE LOAD: once ended, no restart in this view.
  const [completed, setCompleted] = useState(false);
  // True once the provider's video element is actually PLAYING (first
  // delivered frame).
  const [mediaLive, setMediaLive] = useState(false);
  const wasConnected = useRef(false);
  const rootRef = useRef<HTMLElement>(null);
  const backdropRef = useRef<HTMLVideoElement>(null);
  const uuidRef = useRef<string>("");
  const startedAtRef = useRef(0);
  const startClickedAtRef = useRef(0);
  const connectMsRef = useRef<number | null>(null);
  const recorderRef = useRef<{
    stop: () => void;
    stats: () => RecorderStats;
  } | null>(null);
  const chunkLogRef = useRef<ChunkLogEntry[]>([]);
  const samplesRef = useRef<DeliverySample[]>([]);
  const connectivityRef = useRef<ConnectivityProbe | null>(null);
  const manifestSent = useRef(false);
  const errorRef = useRef<string | null>(null);
  errorRef.current = error;
  const onEndRef = useRef(onEnd);
  onEndRef.current = onEnd;

  const sampleDelivery = useCallback(() => {
    const video = rootRef.current?.querySelector(
      "video:not([data-backdrop])",
    ) as HTMLVideoElement | null;
    if (!video || !video.videoWidth) return;
    const sample: DeliverySample = {
      t: Math.round((Date.now() - startedAtRef.current) / 1000),
      w: video.videoWidth,
      h: video.videoHeight,
    };
    const rect = video.getBoundingClientRect();
    sample.displayW = Math.round(rect.width);
    sample.displayH = Math.round(rect.height);
    const src = video.srcObject;
    if (src instanceof MediaStream) {
      const track = src.getVideoTracks()[0];
      const fps = track?.getSettings().frameRate;
      if (fps) sample.fps = Math.round(fps * 10) / 10;
    }
    const q = video.getVideoPlaybackQuality?.();
    if (q) {
      sample.droppedFrames = q.droppedVideoFrames;
      sample.totalFrames = q.totalVideoFrames;
    }
    samplesRef.current.push(sample);
  }, []);

  const sendManifest = useCallback(() => {
    if (manifestSent.current || !uuidRef.current) return;
    manifestSent.current = true;
    const auth = stimulusAuth();
    const events: SessionLogEntry[] = drainSessionLog(uuidRef.current);
    const record = {
      k: auth.k,
      kPayload: auth.payload,
      sessionUuid: uuidRef.current,
      presentationId: presentationId(),
      provider: session.provider,
      meta: session.meta,
      samples: samplesRef.current,
      events,
      connectivity: connectivityRef.current,
      display: {
        devicePixelRatio: window.devicePixelRatio,
        viewportW: window.innerWidth,
        viewportH: window.innerHeight,
        userAgent: navigator.userAgent,
      },
      durationSeconds: startedAtRef.current
        ? Math.round((Date.now() - startedAtRef.current) / 1000)
        : 0,
      connectMs: connectMsRef.current,
      recorded: recorderRef.current !== null,
      recorder: recorderRef.current?.stats() ?? null,
      recordingChunks: chunkLogRef.current,
      endState: wasConnected.current ? "completed" : "error-before-connect",
      errorDetail: errorRef.current,
    };
    postToHost({
      type: "session-ended",
      sessionUuid: uuidRef.current,
      provider: session.provider,
      endState: record.endState,
      connectMs: connectMsRef.current,
      srflx: connectivityRef.current?.srflx ?? null,
    });
    try {
      navigator.sendBeacon(
        "/api/manifest",
        new Blob([JSON.stringify(record)], { type: "application/json" }),
      );
    } catch {
      // Ignore.
    }
  }, [session]);

  // Session semantics: the provider reports connected.
  useEffect(() => {
    if (status === "connected") wasConnected.current = true;
  }, [status]);

  // Poll the shared video element until the first frame is actually
  // playing.
  useEffect(() => {
    if (status !== "connected" || mediaLive) return;
    const poll = setInterval(() => {
      const v = rootRef.current?.querySelector(
        "video:not([data-backdrop])",
      ) as HTMLVideoElement | null;
      if (v && v.videoWidth > 0 && v.currentTime > 0 && !v.paused) {
        clearInterval(poll);
        logSessionEvent(uuidRef.current, "media-live");
        setMediaLive(true);
      }
    }, 200);
    return () => clearInterval(poll);
  }, [status, mediaLive]);

  // Countdown from first delivered frame; sample delivered video and run
  // the evidence recorder from the same anchor.
  useEffect(() => {
    if (!mediaLive) return;
    if (!startedAtRef.current) {
      startedAtRef.current = Date.now();
      if (startClickedAtRef.current) {
        connectMsRef.current = Date.now() - startClickedAtRef.current;
      }
      if (rootRef.current && uuidRef.current) {
        startSessionRecorder(
          rootRef.current,
          uuidRef.current,
          stimulusAuth(),
          (entry) => chunkLogRef.current.push(entry),
        ).then((rec) => {
          recorderRef.current = rec;
        });
      }
    }
    const sampleTimes = [2, 15, 60, SESSION_CAP_SECONDS - 5];
    const interval = setInterval(() => {
      setSecondsLeft((s) => {
        const elapsed = SESSION_CAP_SECONDS - s + 1;
        if (sampleTimes.includes(elapsed)) sampleDelivery();
        if (s <= 1) {
          clearInterval(interval);
          setCompleted(true);
          recorderRef.current?.stop();
          sampleDelivery();
          sendManifest();
          onEndRef.current();
          return 0;
        }
        return s - 1;
      });
    }, 1000);
    return () => clearInterval(interval);
  }, [mediaLive, sampleDelivery, sendManifest]);

  // Ambient fill: mirror the provider's stream into the blurred backdrop.
  useEffect(() => {
    if (status !== "connected") return;
    const sync = () => {
      const fg = rootRef.current?.querySelector(
        "video:not([data-backdrop])",
      ) as HTMLVideoElement | null;
      const bg = backdropRef.current;
      if (fg?.srcObject && bg && bg.srcObject !== fg.srcObject) {
        bg.srcObject = fg.srcObject;
      }
    };
    sync();
    const interval = setInterval(sync, 1000);
    return () => clearInterval(interval);
  }, [status]);

  // A session that was connected and is no longer (user ended it, the cap
  // fired server-side, or the provider closed it) is complete — no restart.
  // Pre-connection FAILURES also send a manifest (endState:
  // "error-before-connect").
  useEffect(() => {
    if (wasConnected.current && (status === "idle" || status === "error")) {
      setCompleted(true);
      recorderRef.current?.stop();
      sampleDelivery();
      sendManifest();
    } else if (status === "error" && uuidRef.current) {
      sendManifest();
    }
  }, [status, sampleDelivery, sendManifest]);

  // Don't lose the manifest if the rater closes/advances mid-session.
  useEffect(() => {
    const onUnload = () => {
      if (wasConnected.current) {
        recorderRef.current?.stop();
        sampleDelivery();
        sendManifest();
      }
    };
    window.addEventListener("beforeunload", onUnload);
    return () => window.removeEventListener("beforeunload", onUnload);
  }, [sampleDelivery, sendManifest]);

  const handleStart = () => {
    // Every attempt is a distinct session entity: fresh UUID, fresh
    // evidence state.
    uuidRef.current = crypto.randomUUID();
    manifestSent.current = false;
    samplesRef.current = [];
    chunkLogRef.current = [];
    connectivityRef.current = null;
    setMediaLive(false);
    setSecondsLeft(SESSION_CAP_SECONDS);
    startClickedAtRef.current = Date.now();
    startSessionLog(uuidRef.current);
    const probeFor = uuidRef.current;
    probeConnectivity().then((probe) => {
      if (uuidRef.current !== probeFor) return;
      connectivityRef.current = probe;
      logSessionEvent(
        probeFor,
        "connectivity-probe",
        probe.error ?? probe.candidateTypes.join("+") ?? null,
      );
    });
    postToHost({
      type: "session-started",
      sessionUuid: uuidRef.current,
      provider: session.provider,
    });
    onStart(uuidRef.current);
  };

  const handleEnd = () => {
    setCompleted(true);
    recorderRef.current?.stop();
    sampleDelivery();
    sendManifest();
    onEnd();
  };

  const topic = pageParams().get("topic");
  const examples = pageParams().get("examples");
  // An instruction, when the URL carries one, replaces the topic chip.
  const instruction = pageParams().get("instruction");

  return (
    <main
      ref={rootRef}
      className="relative z-0 w-screen h-screen bg-black overflow-hidden"
    >
      <video
        ref={backdropRef}
        data-backdrop
        aria-hidden
        muted
        autoPlay
        playsInline
        className="absolute inset-0 -z-10 h-full w-full object-cover pointer-events-none"
        style={{
          filter: "blur(36px) brightness(0.75) saturate(1.25)",
          transform: "scale(1.15)",
        }}
      />
      {children}

      {status === "connecting" ? (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <div className="w-12 h-12 border-4 border-white/30 border-t-white rounded-full animate-spin" />
        </div>
      ) : null}

      {mediaLive && status === "connected" ? (
        <div className="absolute top-4 right-4 z-10 rounded-full bg-black/60 px-4 py-2 font-mono text-white tabular-nums">
          {formatTime(secondsLeft)}
        </div>
      ) : null}

      {instruction && !completed ? (
        <div className="absolute top-4 left-4 z-10 max-w-md rounded-lg bg-black/60 px-4 py-2 text-white">
          <span className="font-medium">{instruction}</span>
        </div>
      ) : topic && !completed ? (
        <div className="absolute top-4 left-4 z-10 max-w-md rounded-lg bg-black/60 px-4 py-2 text-white">
          <span className="font-medium">Topic: {topic}</span>
          {examples ? (
            <span className="text-white/80"> — e.g. {examples}</span>
          ) : null}
        </div>
      ) : null}

      <div className="absolute bottom-8 left-1/2 -translate-x-1/2 z-10">
        {completed && status !== "connected" ? (
          <div className="rounded-full bg-black/70 px-6 py-3 text-white font-medium shadow-lg">
            Session complete — please continue to the ratings below.
          </div>
        ) : status === "connected" ? (
          <button
            onClick={handleEnd}
            className="rounded-full bg-rose-500 hover:bg-rose-600 px-8 py-3 text-white font-medium shadow-lg cursor-pointer"
          >
            End Call
          </button>
        ) : (
          <button
            onClick={handleStart}
            disabled={status === "connecting"}
            className="rounded-full bg-emerald-500 hover:bg-emerald-600 disabled:bg-slate-400 disabled:cursor-not-allowed px-8 py-3 text-white font-medium shadow-lg cursor-pointer"
          >
            {status === "connecting" ? "Connecting…" : "Start Call"}
          </button>
        )}
      </div>

      {error && !completed ? (
        <div className="absolute left-4 bottom-4 z-50 bg-black/70 text-white p-3 rounded-lg max-w-md">
          Connection problem — please press Start Call to try again.
        </div>
      ) : null}
    </main>
  );
};
