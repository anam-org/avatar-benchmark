/// <reference types="astro/client" />
type ManifestKV = {
  put(key: string, value: string | ArrayBuffer): Promise<void>;
};

type WorkerSecrets = {
  ANAM_API_KEY?: string;
  TAVUS_API_KEY?: string;
  HEYGEN_API_KEY?: string;
  // One LiveAvatar context per round: they hold different task prompts.
  LIVEAVATAR_CONTEXT_ID_MAIN?: string;
  LIVEAVATAR_CONTEXT_ID_PRACTICE?: string;
  LEMONSLICE_API_KEY?: string;
  LIVEKIT_URL?: string;
  LIVEKIT_API_KEY?: string;
  LIVEKIT_API_SECRET?: string;
  BENCHMARK_URL_SECRET?: string;
};

type WorkerEnv = WorkerSecrets & { MANIFEST?: ManifestKV };

declare module "cloudflare:workers" {
  export const env: WorkerEnv & Record<string, string | undefined>;
}
