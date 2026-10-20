#!/usr/bin/env python3
"""Print signed stimulus URLs for hand-testing one session per provider.

This builds them from the same config and the same signing code the
dataset uses (`avatar_dataset`).

Use it for the dry-run sessions the launch gate expects,
and for checking a provider integration change before spending on a job.

    python scripts/local_stimulus_urls.py                 # localhost dev
    python scripts/local_stimulus_urls.py --slot m2
    python scripts/local_stimulus_urls.py --training      # practice round
    python scripts/local_stimulus_urls.py --variant livekit
    python scripts/local_stimulus_urls.py --base https://<deployment>

Requires BENCHMARK_URL_SECRET, matching the target's worker secret.
A local dev server needs no deploy: `npx wrangler dev --port 8788`, then
open the URLs. KV
writes go to the local store under .wrangler/state, not to the study's
namespace.
"""

from __future__ import annotations

import urllib.parse
import uuid
import webbrowser

from dotenv import load_dotenv
from fire import Fire

load_dotenv()

# Imported after load_dotenv: the module reads the signing secret at import.
import avatar_dataset as ad  # noqa: E402


def main(
    base: str = "http://localhost:8788",
    slot: str = "f2",
    variant: str = "turnkey",
    providers: str | None = None,
    training: bool = False,
    pid: bool = True,
    open_browser: bool = False,
) -> None:
    """Print one signed stimulus URL per provider.

    Prefer --open-browser to copying: the token and the presentation id sit
    at the END of a long query string, so a URL clipped anywhere in the
    paste silently drops them.

    Args:
      base: origin of the harness under test. Defaults to the dev server.
      slot: avatar slot, as keyed in config/avatars.json. Fixes the topic,
            as in the study.
      variant: turnkey (each provider's own stack) or livekit (face only).
      providers: comma-separated subset. Default: all in the variant.
      training: build the practice round instead of a rated round.
      pid: append a presentation id, as the dataset wrapper does.
      open_browser: open each URL in the default browser instead of relying
        on copy-paste. One tab per provider; run them one at a time.
    """
    pmap = ad.PROVIDERS_LIVEKIT if variant == "livekit" else ad.PROVIDERS
    # A comma-separated --providers value arrives from python-fire as a
    # tuple, not a string.
    if providers is None:
        keys = list(pmap)
    elif isinstance(providers, str):
        keys = [p.strip() for p in providers.split(",") if p.strip()]
    else:
        keys = [str(p).strip() for p in providers if str(p).strip()]

    topic = ad.PRACTICE_TOPIC if training else ad.SLOT_TOPICS[slot]
    ctx_base = {
        "topic": topic,
        "name": ad.AVATAR_NAMES[slot],
        "examples": ad.TOPICS[topic],
        "slot": slot,
        "training": training,
    }

    round_label = "practice" if training else "main"
    print(f"{base}  —  slot {slot}, topic {topic}, {round_label} round, "
          f"{variant}\n")

    for key in keys:
        if key not in pmap:
            raise SystemExit(f"unknown provider for {variant}: {key}")
        # avatars_for resolves an arm to the slot map it rates: an Anam arm
        # shares the base arm's avatars and has no map of its own.
        av = ad.avatars_for(key).get(slot)
        if av is None and variant != "livekit":
            print(f"{key:11} (no avatar configured for slot {slot})")
            continue
        page_path, build_params = pmap[key]
        params = build_params(av or {}, {**ctx_base, "provider_key": key})
        if training:
            params["training"] = "true"
        if pid:
            params["pid"] = str(uuid.uuid4())
        url = f"{base.rstrip('/')}{page_path}?{urllib.parse.urlencode(params)}"
        print(f"{key:11} {url}\n")
        if open_browser:
            webbrowser.open(url)

    print("Open one at a time, allow the microphone, press Start Call, and "
          "check the avatar speaks first without being prompted.")
    if not open_browser:
        print("If the topic chip's examples look cut off, the URL was clipped "
              "in the paste and the token went with it — use --open-browser.")


if __name__ == "__main__":
    Fire(main)
