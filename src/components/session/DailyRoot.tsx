import DailyIframe from "@daily-co/daily-js";
import { DailyProvider } from "@daily-co/daily-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

/**
 * Shared Daily call-object lifecycle for the Daily-transported providers
 * (Tavus, LemonSlice). Creates one call object per mount — mic on, camera
 * off (raters are audio-only participants) — and tears it down cleanly.
 */
export const DailyRoot = ({ children }: { children: ReactNode }) => {
  const callObjectRef = useRef<ReturnType<
    typeof DailyIframe.createCallObject
  > | null>(null);
  const [isReady, setIsReady] = useState(false);

  useEffect(() => {
    if (callObjectRef.current) return;
    callObjectRef.current = DailyIframe.createCallObject({
      videoSource: false,
      audioSource: true,
    });
    setIsReady(true);

    return () => {
      const callObject = callObjectRef.current;
      if (!callObject) return;
      try {
        const state = callObject.meetingState();
        if (state === "joined-meeting" || state === "joining-meeting") {
          callObject.leave();
        }
      } catch {
        // Ignore.
      }
      try {
        callObject.destroy();
      } catch {
        // Ignore.
      }
      callObjectRef.current = null;
    };
  }, []);

  if (!isReady || !callObjectRef.current) {
    return <main className="w-screen h-screen bg-black" />;
  }

  return (
    <DailyProvider callObject={callObjectRef.current}>{children}</DailyProvider>
  );
};

/** Leave the Daily call if it is active; swallow cleanup errors. */
export const leaveIfJoined = async (
  callObject: ReturnType<typeof DailyIframe.createCallObject> | null,
) => {
  if (!callObject) return;
  try {
    const state = callObject.meetingState();
    if (state === "joined-meeting" || state === "joining-meeting") {
      await callObject.leave();
    }
  } catch {
    // Ignore leave errors.
  }
};
