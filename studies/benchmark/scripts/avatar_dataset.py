"""Local dataset generation for the digital-avatar frontends.

All study configuration (avatars, experiment definition, provider routes)
lives in ../config/*.json. This module loads it and provides:
- Per-provider query-param builders + the PROVIDERS map.
- Writing the folder dataset of iframe-wrapper JS files + zipping it.

The Mabyduck API client lives in `packages/mabyduck`; the CLI entrypoint is
`create_mabyduck_dataset.py`.
"""

from __future__ import annotations

import functools
import json
import os
import shutil
import urllib.parse
from pathlib import Path
from typing import Callable, Dict

from stimulus import bind, build_iframe_js, core_payload, sign, zip_directory

# -- Config (single source of truth: ../config/*.json) ---------------------
#
# This module is the benchmark study's dataset builder and loads the
# study's own config. Helpers shared with other studies live in the
# `stimulus` package, which loads no config at all.

_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

_AVATARS_CFG = json.loads((_CONFIG_DIR / "avatars.json").read_text())
_EXPERIMENT_CFG = json.loads((_CONFIG_DIR / "experiment.json").read_text())
_PROVIDERS_CFG = json.loads((_CONFIG_DIR / "providers.json").read_text())

# Public URL of the deployed harness worker; the dataset's iframe stimuli
# point here. Must be set to YOUR deployment before building a dataset.
DEFAULT_BASE_URL = os.environ.get("BENCHMARK_BASE_URL", "")



def sign_token(payload: str, *, ttl_days: int | None = None) -> str:
    """Sign a stimulus payload. See stimulus.token for the token format."""
    return sign(payload, ttl=ttl_days)


def token_payload(
    provider: str,
    id_: str,
    ctx: dict,
    *,
    voice: str = "",
    model: str = "",
    avatar_model: str = "",
) -> str:
    """Token payload for this study.

    The six core segments come from stimulus.core_payload and cannot be
    varied here. One study segment is appended after them: the avatar
    model an Anam arm pins. It is dropped when empty.

    The provider segment is the mint route, `anam` for every Anam arm.
    """
    core = core_payload(
        provider=provider,
        id_=id_,
        topic=ctx["topic"],
        mode="practice" if ctx.get("training") else "main",
        voice=voice,
        model=model,
    )
    return bind(core, avatar_model)


SLOT_TOPICS: Dict[str, str] = _AVATARS_CFG["slotTopics"]
PRACTICE_TOPIC: str = _AVATARS_CFG["practiceTopic"]
MAIN_SLOTS: tuple[str, ...] = tuple(_AVATARS_CFG["mainSlots"])
TRAINING_SLOT: str = _AVATARS_CFG["trainingSlot"]
AVATAR_NAMES: Dict[str, str] = _AVATARS_CFG["names"]
AVATARS: Dict[str, Dict[str, Dict[str, str]]] = _AVATARS_CFG["providers"]

DEFAULT_QUESTIONS: list[dict] = _EXPERIMENT_CFG["questions"]
DEFAULT_CATEGORIES: list[dict] = _EXPERIMENT_CFG["categories"]
DEFAULT_INTRODUCTION: str = _EXPERIMENT_CFG["introduction"]
DEFAULT_IFRAME_SIZE: dict = _EXPERIMENT_CFG["iframeSize"]
TOPICS: Dict[str, str] = _EXPERIMENT_CFG["topics"]

_PAGE_PATHS: Dict[str, str] = _PROVIDERS_CFG["pagePaths"]
ANAM_MODEL: str = _PROVIDERS_CFG["anam"]["model"]
_LK_CFG = json.loads((_CONFIG_DIR / "livekit.json").read_text())


# -- Per-provider param builders ------------------------------------------


def build_heygen(av: dict, ctx: dict) -> dict:
    return {
        "avatarId": av["avatarId"],
        "voiceId": av["voiceId"],
        "topic": ctx["topic"],
        "name": ctx["name"],
        "examples": ctx["examples"],
        "k": sign_token(token_payload("heygen", av["avatarId"], ctx, voice=av["voiceId"])),
    }


def build_lemonslice(av: dict, ctx: dict) -> dict:
    # The page chip shows the agent's own topic; training rounds use the
    # dedicated practice agent. The group topic is ignored for this provider.
    if ctx.get("training"):
        agent = AVATARS["lemonslice"]["practice"]
    else:
        agent = av
    topic = agent["fixedTopic"]
    return {
        "avatarId": agent["avatarId"],
        "topic": topic,
        "name": ctx["name"],
        "examples": TOPICS[topic],
        "k": sign_token(token_payload("lemonslice", agent["avatarId"], {**ctx, "topic": topic})),
    }


def build_tavus(av: dict, ctx: dict) -> dict:
    return {
        "replicaId": av["replicaId"],
        "topic": ctx["topic"],
        "name": ctx["name"],
        "examples": ctx["examples"],
        "k": sign_token(token_payload("tavus", av["replicaId"], ctx)),
    }


def build_anam(av: dict, ctx: dict, *, avatar_model: str = "") -> dict:
    """Anam stimulus params. `avatar_model` pins the avatar model for
    an arm other than the deployed default; empty means the default."""
    params = {
        "avatarId": av["avatarId"],
        "voiceId": av["voiceId"],
        "name": ctx["name"],
        "topic": ctx["topic"],
        "model": ANAM_MODEL,
        "examples": ctx["examples"],
        "k": sign_token(
            token_payload(
                "anam",
                av["avatarId"],
                ctx,
                voice=av["voiceId"],
                model=ANAM_MODEL,
                avatar_model=avatar_model,
            )
        ),
    }
    if avatar_model:
        params["avatarModel"] = avatar_model
    return params


def build_livekit(av: dict, ctx: dict) -> dict:
    # LiveKit study: avatar IDs resolve server-side from config; the URL
    # carries only the slot + round context.
    return {
        "slot": ctx["slot"],
        "topic": ctx["topic"],
        "name": ctx["name"],
        "examples": ctx["examples"],
        "k": sign_token(":".join([
            "livekit", ctx["provider_key"], ctx["slot"], ctx["topic"],
            "practice" if ctx.get("training") else "main",
        ])),
    }


# (page path on the worker, query-param builder) per arm key.
PROVIDERS: Dict[str, tuple[str, Callable[[dict, dict], dict]]] = {
    "heygen": (_PAGE_PATHS["heygen"], build_heygen),
    "lemonslice": (_PAGE_PATHS["lemonslice"], build_lemonslice),
    "tavus": (_PAGE_PATHS["tavus"], build_tavus),
    "anam": (_PAGE_PATHS["anam"], build_anam),
}

# Extra Anam arms declared in providers.json. Each is a full arm to the
# dataset — its own folder entry, its own rating condition — but shares the
# base arm's page, avatars and voices, so the only thing that varies is the
# avatar model.
ANAM_ARMS: Dict[str, str] = {
    key: cfg["avatarModel"] for key, cfg in _PROVIDERS_CFG["anam"].get("arms", {}).items()
}
for _arm, _model in ANAM_ARMS.items():
    PROVIDERS[_arm] = (
        _PAGE_PATHS["anam"],
        functools.partial(build_anam, avatar_model=_model),
    )


# Extra Tavus arms declared in providers.json. Each carries its own slot map
# in avatars.json under the arm key, which is where it differs from an Anam
# arm: Tavus does not serve one replica on two models, so a model arm is
# necessarily a different cast. Page and mint route stay the base arm's — the
# replica id is what selects the model, and the token's provider segment must
# keep matching the route the page posts to, which is `tavus` for every Tavus
# arm.
TAVUS_ARMS: Dict[str, str] = {
    key: cfg["modelName"] for key, cfg in _PROVIDERS_CFG["tavus"].get("arms", {}).items()
}
for _arm in TAVUS_ARMS:
    PROVIDERS[_arm] = (_PAGE_PATHS["tavus"], build_tavus)


def avatars_for(provider: str) -> Dict[str, Dict[str, str]]:
    """Slot map for an arm.

    Extra Anam arms have no slots of their own and rate the base arm's
    avatars. Extra Tavus arms each have their own entry under the arm key."""
    return AVATARS.get("anam" if provider in ANAM_ARMS else provider, {})

# LiveKit study: one page per arm. The whole study runs through a single page
# and a single mint route, so an arm is addressed by its own key throughout —
# route, signed token and dispatch metadata all carry the same string, and the
# agent resolves the cast and the avatar model from it.
PROVIDERS_LIVEKIT: Dict[str, tuple[str, Callable[[dict, dict], dict]]] = {
    key: (path, build_livekit) for key, path in _LK_CFG["pagePaths"].items()
}
for _arm in (*ANAM_ARMS, *TAVUS_ARMS):
    PROVIDERS_LIVEKIT[_arm] = (f"/livekit/{_arm}", build_livekit)


# -- Folder builder --------------------------------------------------------


def build_config(topic: str, examples: str) -> dict:
    # Header intentionally empty: topic + examples render INSIDE the session
    # page (SessionShell overlay).
    return {"title": "", "question": "", "description": []}


def _require_base(base_url: str) -> str:
    """Stimulus URLs are absolute: the rating platform loads them from its
    own origin. An empty base yields relative URLs that resolve nowhere."""
    if not base_url:
        raise SystemExit(
            "BENCHMARK_BASE_URL not set — stimulus URLs would be relative"
            " and the dataset would not load on the rating platform")
    return base_url


def build_folder(
    *,
    out_root: Path,
    provider_list: list[str],
    training: bool,
    base_url: str = DEFAULT_BASE_URL,
    topics: dict[str, str] = TOPICS,
    providers_map: Dict[str, tuple[str, Callable[[dict, dict], dict]]] | None = None,
) -> tuple[int, int]:
    """Write the group folders + per-provider .js files. Returns
    (groups_written, js_written)."""
    # Fail on an arm with no cast.
    castless = [p for p in provider_list if not avatars_for(p)]
    if castless:
        raise SystemExit(
            f"no cast in config/avatars.json for: {', '.join(castless)}. "
            f"Fill in its slots, or drop the provider from this run."
        )
    base_url = _require_base(base_url)
    if out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True)

    groups_written = 0
    js_written = 0

    # Training is a single round: the trainingSlot avatar, the practice topic.
    # Main rounds: one group per slot, topic hard-linked via SLOT_TOPICS.
    if training:
        slot_topic_pairs = [(TRAINING_SLOT, PRACTICE_TOPIC)]
    else:
        slot_topic_pairs = [(k, SLOT_TOPICS[k]) for k in MAIN_SLOTS]

    for avatar_key, topic in slot_topic_pairs:
        examples = TOPICS[topic]
        group = f"{topic}_{avatar_key}"
        group_dir = out_root / group
        group_dir.mkdir()

        (group_dir / "config.json").write_text(
            json.dumps(build_config(topic, examples), indent=2) + "\n",
            encoding="utf-8",
        )
        groups_written += 1

        for provider in provider_list:
            pmap = providers_map or PROVIDERS
            av = avatars_for(provider).get(avatar_key)
            if av is None:
                continue

            page_path, build_params = pmap[provider]
            params = build_params(
                av,
                {
                    "topic": topic,
                    "name": AVATAR_NAMES[avatar_key],
                    "examples": examples,
                    "slot": avatar_key,
                    "provider_key": provider,
                    "training": training,
                },
            )
            if training:
                params["training"] = "true"
            qs = urllib.parse.urlencode(params)
            src = f"{base_url.rstrip('/')}{page_path}?{qs}"

            (group_dir / f"{provider}.js").write_text(
                build_iframe_js(src), encoding="utf-8"
            )
            js_written += 1

    return groups_written, js_written


