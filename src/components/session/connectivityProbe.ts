/**
 * Connectivity probe — one per session.
 *
 * It gathers ICE candidates against Cloudflare's public STUN server and
 * reports which candidate types the network permitted:
 *
 * - `host` only            → no reflexive address was learned
 * - `host` + `srflx`       → a public reflexive address was learned
 */

export type ConnectivityProbe = {
  /** Distinct ICE candidate types gathered, e.g. ["host", "srflx"]. */
  candidateTypes: string[];
  /** True once a server-reflexive candidate was learned (STUN reachable). */
  srflx: boolean;
  /** ms from probe start to the first srflx candidate, null if none. */
  srflxMs: number | null;
  /** ms from probe start to gathering complete (or timeout). */
  gatherMs: number;
  /** Gathering finished on its own rather than hitting the timeout. */
  complete: boolean;
  /** Transport protocols seen on candidates, e.g. ["udp"]. */
  protocols: string[];
  error?: string;
  /** navigator.connection, where the browser exposes it. */
  effectiveType?: string;
  rtt?: number;
  downlink?: number;
  online?: boolean;
};

const STUN_URL = "stun:stun.cloudflare.com:3478";
const GATHER_TIMEOUT_MS = 4000;

const networkInfo = (): Partial<ConnectivityProbe> => {
  const nav = navigator as Navigator & {
    connection?: { effectiveType?: string; rtt?: number; downlink?: number };
  };
  return {
    online: nav.onLine,
    effectiveType: nav.connection?.effectiveType,
    rtt: nav.connection?.rtt,
    downlink: nav.connection?.downlink,
  };
};

export const probeConnectivity = async (): Promise<ConnectivityProbe> => {
  const started = Date.now();
  const base: ConnectivityProbe = {
    candidateTypes: [],
    srflx: false,
    srflxMs: null,
    gatherMs: 0,
    complete: false,
    protocols: [],
    ...networkInfo(),
  };
  if (typeof RTCPeerConnection === "undefined") {
    return { ...base, error: "RTCPeerConnection unavailable" };
  }

  let pc: RTCPeerConnection | null = null;
  try {
    pc = new RTCPeerConnection({ iceServers: [{ urls: STUN_URL }] });
    const types = new Set<string>();
    const protocols = new Set<string>();
    let srflxMs: number | null = null;

    const done = new Promise<boolean>((resolve) => {
      const timer = setTimeout(() => resolve(false), GATHER_TIMEOUT_MS);
      const finish = (complete: boolean) => {
        clearTimeout(timer);
        resolve(complete);
      };
      pc!.onicecandidate = (ev) => {
        if (!ev.candidate) {
          finish(true);
          return;
        }
        const { type, protocol } = ev.candidate;
        if (type) types.add(type);
        if (protocol) protocols.add(protocol);
        if (type === "srflx" && srflxMs === null) {
          srflxMs = Date.now() - started;
          finish(true);
        }
      };
    });

    // A data channel is enough to make the connection gather candidates.
    pc.createDataChannel("probe");
    await pc.setLocalDescription(await pc.createOffer());
    const complete = await done;

    return {
      ...base,
      candidateTypes: [...types],
      protocols: [...protocols],
      srflx: types.has("srflx"),
      srflxMs,
      gatherMs: Date.now() - started,
      complete,
    };
  } catch (err) {
    return {
      ...base,
      gatherMs: Date.now() - started,
      error: err instanceof Error ? err.message : String(err),
    };
  } finally {
    try {
      pc?.close();
    } catch {
      // Ignore.
    }
  }
};
