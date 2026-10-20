import {
  Room,
  RoomEvent,
  Track,
  type RemoteTrack,
} from "livekit-client";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  SessionShell,
  VIDEO_CLASSES,
  type SessionStatus,
} from "../session/SessionShell";

/**
 * LiveKit study condition.
 *
 * The rater joins a LiveKit room; /api/livekit dispatches the benchmark
 * agent, which attaches the provider's avatar as a room participant. This
 * client only publishes the mic and renders whatever video/audio the avatar
 * participant publishes. It takes the provider as a prop and forwards it to
 * the mint route.
 */

type Props = {
  provider: string;
  /** Avatar slot, as keyed in config/avatars.json. Named avatarSlot
   * because `slot` is a reserved
   *  attribute in Astro templates (named-slot placement). */
  avatarSlot: string;
  topic?: string;
  name?: string;
  training?: boolean;
};

export const LiveKitAvatar = ({
  provider,
  avatarSlot,
  topic,
  name,
  training,
}: Props) => {
  const videoRef = useRef<HTMLVideoElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const roomRef = useRef<Room | null>(null);
  const [status, setStatus] = useState<SessionStatus>("idle");
  const [error, setError] = useState<string | null>(null);

  const endCall = useCallback(() => {
    roomRef.current?.disconnect();
    roomRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    if (audioRef.current) audioRef.current.srcObject = null;
    setStatus("idle");
  }, []);

  const startCall = useCallback(
    async (sessionUuid: string) => {
      setError(null);
      setStatus("connecting");
      try {
        const res = await fetch("/api/livekit", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            provider,
            slot: avatarSlot,
            topic,
            name,
            training,
            sessionUuid,
            k: new URLSearchParams(window.location.search).get("k"),
          }),
        });
        const payload = await res.json().catch(() => ({}));
        if (!res.ok) {
          throw new Error(payload?.error || `Session create failed (${res.status})`);
        }

        const room = new Room();
        roomRef.current = room;

        room.on(RoomEvent.TrackSubscribed, (track: RemoteTrack) => {
          if (track.kind === Track.Kind.Video && videoRef.current) {
            track.attach(videoRef.current);
            setStatus("connected");
          }
          if (track.kind === Track.Kind.Audio && audioRef.current) {
            track.attach(audioRef.current);
          }
        });
        room.on(RoomEvent.Disconnected, () => {
          roomRef.current = null;
          setStatus("idle");
        });

        await room.connect(payload.url, payload.token);
        await room.localParticipant.setMicrophoneEnabled(true);
      } catch (err) {
        console.error("LiveKit error:", err);
        setError(
          err instanceof Error ? err.message : "Failed to start session",
        );
        setStatus("error");
        roomRef.current?.disconnect();
        roomRef.current = null;
      }
    },
    [provider, avatarSlot, topic, name, training],
  );

  useEffect(() => {
    return () => {
      roomRef.current?.disconnect();
      roomRef.current = null;
    };
  }, []);

  return (
    <SessionShell
      status={status}
      error={error}
      onStart={startCall}
      onEnd={endCall}
      session={{ provider: `livekit-${provider}`, meta: { slot: avatarSlot, topic } }}
    >
      <video
        ref={videoRef}
        autoPlay
        playsInline
        className={VIDEO_CLASSES}
      />
      <audio ref={audioRef} autoPlay />
    </SessionShell>
  );
};
