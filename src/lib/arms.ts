import avatarsConfig from "../config/avatars.json";
import providersConfig from "../config/providers.json";

/**
 * Arms declared in config/providers.json, and how each resolves to the cast
 * and model it runs.
 *
 * An Anam arm pins an avatar model and rates the base arm's cast. A Tavus
 * arm carries its own cast under its own key in config/avatars.json.
 */

/** Anam arm key -> the avatar model it pins. */
export const ANAM_ARMS: Readonly<Record<string, string>> = Object.fromEntries(
  Object.entries(providersConfig.anam.arms ?? {}).map(([key, arm]) => [
    key,
    arm.avatarModel,
  ]),
);

/** Tavus arm key -> the model name its replicas serve. */
export const TAVUS_ARMS: Readonly<Record<string, string>> = Object.fromEntries(
  Object.entries(providersConfig.tavus.arms ?? {}).map(([key, arm]) => [
    key,
    arm.modelName,
  ]),
);

const SLOTS = avatarsConfig.providers as unknown as Record<
  string,
  Record<string, Record<string, string>>
>;

/** Slot map for an arm, or undefined if the key names no arm. */
export function slotsFor(
  provider: string,
): Record<string, Record<string, string>> | undefined {
  return SLOTS[provider in ANAM_ARMS ? "anam" : provider];
}

/** The base provider an arm runs on: its plugin, and its page in the turnkey study. */
export function baseProvider(provider: string): string {
  if (provider in ANAM_ARMS) return "anam";
  if (provider in TAVUS_ARMS) return "tavus";
  return provider;
}

/** The avatar model an arm pins. Empty means the configured default. */
export function avatarModelFor(provider: string): string {
  return ANAM_ARMS[provider] ?? "";
}
