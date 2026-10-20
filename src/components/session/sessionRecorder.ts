/**
 * Session A/V evidence recorder.
 *
 * Records ONE WebM per session containing:
 * - the avatar's video track (as delivered — no re-render), and
 * - a stereo audio mix: LEFT = the rater's microphone, RIGHT = the
 *   avatar's audio.
 *
 * The recorder:
 * - WAITS for a video track (up to VIDEO_WAIT_MS) before starting the
 *   MediaRecorder (tracks cannot be added once started);
 * - scans the WHOLE document for audio sinks, root-only for video. The
 *   rater's microphone is never rendered as a playback element, so a
 *   document-wide audio scan picks up avatar output only;
 * - keeps scanning during the session and mixes newly appearing audio
 *   streams into the avatar channel;
 * - reports what it actually captured (recorder stats).
 *
 * Chunks are uploaded every 5s to /api/recording (sequential, per-session
 * UUID + sequence number). Recording is best-effort.
 */

export const RECORDER_VERSION = 2;

const VIDEO_BPS = 600_000;
const AUDIO_BPS = 64_000;
const CHUNK_MS = 5_000;
const VIDEO_WAIT_MS = 20_000;
const SCAN_INTERVAL_MS = 500;

export type RecorderStats = {
  version: number;
  videoCaptured: boolean;
  audioStreamsCaptured: number;
  startDelayMs: number;
};

type Recorder = { stop: () => void; stats: () => RecorderStats };

const discoverTracks = (root: HTMLElement) => {
  const videoTracks: MediaStreamTrack[] = [];
  // Video: the avatar surface inside the shell (backdrop mirror excluded).
  for (const el of root.querySelectorAll<HTMLVideoElement>(
    "video:not([data-backdrop])",
  )) {
    const src = el.srcObject;
    if (src instanceof MediaStream) videoTracks.push(...src.getVideoTracks());
  }
  // Audio: any playback element in the document.
  const audioStreams: MediaStream[] = [];
  for (const el of document.querySelectorAll<HTMLMediaElement>(
    "video:not([data-backdrop]), audio",
  )) {
    const src = el.srcObject;
    if (src instanceof MediaStream && src.getAudioTracks().length > 0) {
      audioStreams.push(src);
    }
  }
  return { videoTracks, audioStreams };
};

export type ChunkLogEntry = { seq: number; atMs: number; bytes: number };

export const startSessionRecorder = async (
  root: HTMLElement,
  sessionUuid: string,
  auth: { k: string; payload: string },
  onChunk?: (entry: ChunkLogEntry) => void,
): Promise<Recorder | null> => {
  try {
    const t0 = Date.now();

    // Stereo mix: L = rater mic, R = avatar audio.
    const ctx = new AudioContext();
    const merger = ctx.createChannelMerger(2);
    const mic = await navigator.mediaDevices.getUserMedia({ audio: true });
    ctx.createMediaStreamSource(mic).connect(merger, 0, 0);
    const mixDest = ctx.createMediaStreamDestination();
    merger.connect(mixDest);

    const connectedAudio = new Set<string>();
    const connectAudio = (streams: MediaStream[]) => {
      for (const s of streams) {
        const key = s
          .getAudioTracks()
          .map((t) => t.id)
          .join("/");
        if (!key || connectedAudio.has(key)) continue;
        connectedAudio.add(key);
        ctx.createMediaStreamSource(s).connect(merger, 0, 1);
      }
    };

    // Wait for the avatar's video track — MediaRecorder cannot add tracks
    // later.
    let videoTrack: MediaStreamTrack | undefined;
    while (Date.now() - t0 < VIDEO_WAIT_MS) {
      const { videoTracks, audioStreams } = discoverTracks(root);
      connectAudio(audioStreams);
      if (videoTracks[0]) {
        videoTrack = videoTracks[0];
        break;
      }
      await new Promise((r) => setTimeout(r, SCAN_INTERVAL_MS));
    }
    const startDelayMs = Date.now() - t0;

    const tracks: MediaStreamTrack[] = [...mixDest.stream.getAudioTracks()];
    if (videoTrack) tracks.push(videoTrack);
    const recorder = new MediaRecorder(new MediaStream(tracks), {
      mimeType: MediaRecorder.isTypeSupported("video/webm;codecs=vp8,opus")
        ? "video/webm;codecs=vp8,opus"
        : "video/webm",
      videoBitsPerSecond: VIDEO_BPS,
      audioBitsPerSecond: AUDIO_BPS,
    });

    // Late audio sinks (provider reconnects, secondary elements) keep
    // getting mixed in for the life of the session.
    const rescan = setInterval(() => {
      connectAudio(discoverTracks(root).audioStreams);
    }, 1000);

    let seq = 0;
    let uploading = Promise.resolve();
    recorder.ondataavailable = (ev) => {
      if (!ev.data || ev.data.size === 0) return;
      const n = seq++;
      // Wall-clock per chunk.
      onChunk?.({ seq: n, atMs: Date.now() - t0, bytes: ev.data.size });
      // Sequential uploads preserve order and avoid saturating the uplink.
      uploading = uploading.then(() =>
        fetch(
          `/api/recording?uuid=${sessionUuid}&seq=${n}&k=${auth.k}&kp=${encodeURIComponent(auth.payload)}`,
          {
            method: "POST",
            body: ev.data,
            keepalive: ev.data.size < 60_000,
          },
        )
          .then(() => undefined)
          .catch(() => undefined),
      );
    };
    recorder.start(CHUNK_MS);

    return {
      stats: () => ({
        version: RECORDER_VERSION,
        videoCaptured: videoTrack !== undefined,
        audioStreamsCaptured: connectedAudio.size,
        startDelayMs,
      }),
      stop: () => {
        try {
          clearInterval(rescan);
          if (recorder.state !== "inactive") recorder.stop();
          mic.getTracks().forEach((t) => t.stop());
          ctx.close().catch(() => undefined);
        } catch {
          // Best-effort.
        }
      },
    };
  } catch (err) {
    console.error("session recorder unavailable:", err);
    return null;
  }
};
