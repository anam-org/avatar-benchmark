import type { ProviderAdapter } from "./types";
import { anam } from "./anam";
import { tavus } from "./tavus";
import { heygen } from "./heygen";
import { lemonslice } from "./lemonslice";
import { livekit } from "./livekit";

export type { MintContext, Mint, ProviderAdapter } from "./types";

/** Keyed by its /api/{key} route segment. */
export const PROVIDERS: Record<string, ProviderAdapter> = {
  anam,
  tavus,
  heygen,
  lemonslice,
  livekit,
};
