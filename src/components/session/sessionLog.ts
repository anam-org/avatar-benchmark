/**
 * Per-session event log.
 *
 * Provider components sit INSIDE SessionShell. They append timestamped
 * milestones here, keyed by the session UUID the shell already passes them,
 * and the shell drains the log into the evidence manifest when the session
 * ends.
 *
 * Timestamps are milliseconds since the session's Start Call click.
 */

export type SessionLogEntry = {
  /** ms since Start Call */
  t: number;
  event: string;
  detail?: string | number | boolean | null;
};

/** Manifest writes are size-capped. */
const MAX_ENTRIES = 120;

const entries = new Map<string, SessionLogEntry[]>();
const origins = new Map<string, number>();

export const startSessionLog = (uuid: string): void => {
  if (!uuid) return;
  entries.set(uuid, []);
  origins.set(uuid, Date.now());
  // One session per page load in practice, but a refresh reuses the module:
  // keep only the live session plus the one before it.
  if (entries.size > 2) {
    const oldest = entries.keys().next().value;
    if (oldest && oldest !== uuid) {
      entries.delete(oldest);
      origins.delete(oldest);
    }
  }
};

export const logSessionEvent = (
  uuid: string,
  event: string,
  detail?: SessionLogEntry["detail"],
): void => {
  const log = entries.get(uuid);
  if (!log || log.length >= MAX_ENTRIES) return;
  const origin = origins.get(uuid) ?? Date.now();
  log.push({
    t: Date.now() - origin,
    event,
    ...(detail === undefined ? {} : { detail }),
  });
};

export const drainSessionLog = (uuid: string): SessionLogEntry[] =>
  entries.get(uuid) ?? [];
