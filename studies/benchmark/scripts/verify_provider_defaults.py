#!/usr/bin/env python3
"""Catalog conformance: pinned study config vs live provider catalogs.

Three properties of the study config must hold at launch:

  1. Anam: the harness sends avatarModel (config/providers.json) on every
     mint. The pin must RESOLVE for every study avatar — the pinned model
     appears in that avatar's availableVersions. Each avatar's activeVersion,
     what a bare API call with no avatarModel resolves to, is recorded
     alongside it, so the dump carries both values per avatar.
  2. Anam voices: every configured voice is a stock catalogue voice, on one
     uniform TTS model.
  3. HeyGen: config/avatars.json passes an explicit voice_id per avatar.
     The public avatars API exposes default_voice.

Writes the evidence dump to manifest_data/provider_defaults.json. Run
standalone or via launch_gate.py, which fails the gate on any mismatch.

A further check (--sweep-anam-sessions) verifies not the catalog but the
FIELDED RUN: the /v1/sessions list returns a personaConfig snapshot taken
at session start (llmConfig, avatarVersion). The window is the Anam mint
span of the evidence bundle fielded last, unless --since/--until are given. Evidence goes to manifest_data/anam_session_sweep.json.

Requires ANAM_API_KEY and HEYGEN_API_KEY. A missing key is a
FAILURE, not a skip.

Exit code 0 = all three properties hold (and, with --sweep-anam-sessions,
all fielded sessions ran the pinned stack).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import _env

ROOT = Path(__file__).parent.parent
EVIDENCE = ROOT / "manifest_data" / "provider_defaults.json"


_UA = "Mozilla/5.0 (compatible; anam-avatar-benchmark verify_provider_defaults)"


def _get(url: str, headers: dict[str, str]) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, **headers})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _get_all_pages(url: str, headers: dict[str, str], *,
                   per_page: int = 100, max_pages: int = 1000) -> list[dict]:
    """Follow ?page= pagination to exhaustion."""
    sep = "&" if "?" in url else "?"
    out: list[dict] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        batch = _items(_get(f"{url}{sep}page={page}&perPage={per_page}", headers))
        fresh = [r for r in batch
                 if isinstance(r, dict) and r.get("id") not in seen]
        if not fresh:
            return out
        seen.update(r.get("id") for r in fresh)
        out.extend(fresh)
        if len(batch) < per_page:
            return out
    raise SystemExit(f"FATAL: pagination did not terminate after {max_pages} pages")


def _items(payload: object) -> list[dict]:
    """Unwrap the list of avatar records from common envelope shapes."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "avatars", "items", "results"):
            inner = payload.get(key)
            if isinstance(inner, list):
                return inner
            if isinstance(inner, dict):
                return _items(inner)
    raise SystemExit(f"FATAL: unrecognized catalog response shape: {type(payload)}")


def check_anam(env: dict[str, str], cfg: dict, providers_cfg: dict,
               evidence: dict) -> list[str]:
    key = env.get("ANAM_API_KEY")
    if not key:
        return ["ANAM_API_KEY not set (environment or .env)"]
    pinned = providers_cfg["anam"]["model"]
    # Every model the study can ask for: the base model and each arm's pin.
    arm_models = sorted({
        a["avatarModel"]
        for a in (providers_cfg["anam"].get("arms") or {}).values()
        if a.get("avatarModel")
    })
    catalog = _get_all_pages(
        "https://api.anam.ai/v1/avatars", {"Authorization": f"Bearer {key}"})
    by_id = {a.get("id"): a for a in catalog if isinstance(a, dict)}

    fails: list[str] = []
    for slot, entry in cfg["providers"]["anam"].items():
        avatar_id = entry["avatarId"]
        record = by_id.get(avatar_id)
        active = record.get("activeVersion") if record else None
        available = record.get("availableVersions") if record else None
        evidence["anam"][slot] = {
            "avatarId": avatar_id,
            "pinnedModel": pinned,
            "armModels": arm_models,
            "activeVersion": active,
            "availableVersions": available,
            # Recorded as evidence; not a failure.
            "pinOverridesActiveVersion": bool(record) and active != pinned,
        }
        if record is None:
            fails.append(
                f"anam {slot}: avatar {avatar_id} not in catalog"
                f" ({len(catalog)} avatars read)")
        else:
            have = available if isinstance(available, list) else []
            for model in [pinned, *arm_models]:
                if model not in have:
                    fails.append(
                        f"anam {slot}: model {model!r} not in"
                        f" availableVersions={have!r} — it cannot resolve"
                        " for this avatar and the session would render on"
                        " another model"
                    )
    return fails


def check_anam_voices(env: dict[str, str], cfg: dict, evidence: dict) -> list[str]:
    """Each voice exists in /v1/voices, is stock (createdByOrganizationId
    null — not cloned or custom), and provider and model are the same across
    them.
    """
    key = env.get("ANAM_API_KEY")
    if not key:
        return ["ANAM_API_KEY not set (environment or .env)"]
    voices = {v["id"]: v for v in _get_all_pages(
        "https://api.anam.ai/v1/voices", {"Authorization": f"Bearer {key}"})}
    fails: list[str] = []
    seen: set[tuple] = set()
    for slot, entry in cfg["providers"]["anam"].items():
        vid = entry.get("voiceId")
        rec = voices.get(vid)
        evidence.setdefault("anam_voices", {})[slot] = {
            "voiceId": vid,
            "displayName": (rec or {}).get("displayName"),
            "provider": (rec or {}).get("provider"),
            "providerModelId": (rec or {}).get("providerModelId"),
            "isStock": bool(rec) and not rec.get("createdByOrganizationId"),
        }
        if rec is None:
            fails.append(f"anam {slot}: voice {vid} not in /v1/voices")
            continue
        if rec.get("createdByOrganizationId"):
            fails.append(
                f"anam {slot}: voice {rec.get('displayName')} is org-created,"
                " not a stock catalogue voice")
        seen.add((rec.get("provider"), rec.get("providerModelId")))
    if len(seen) > 1:
        fails.append(f"anam voices are not uniform in provider/model: {sorted(seen)}")
    return fails


def check_heygen(env: dict[str, str], cfg: dict, evidence: dict) -> list[str]:
    key = env.get("HEYGEN_API_KEY")
    if not key:
        return ["HEYGEN_API_KEY not set (environment or .env)"]
    catalog = _items(_get(
        "https://api.liveavatar.com/v1/avatars/public",
        {"X-API-KEY": key},
    ))
    by_id: dict[str, dict] = {}
    for a in catalog:
        if isinstance(a, dict):
            by_id[a.get("avatar_id") or a.get("id")] = a

    fails: list[str] = []
    for slot, entry in cfg["providers"]["heygen"].items():
        avatar_id, voice_id = entry["avatarId"], entry["voiceId"]
        record = by_id.get(avatar_id)
        default_voice = record.get("default_voice") if record else None
        if isinstance(default_voice, dict):
            default_voice = default_voice.get("voice_id") or default_voice.get("id")
        evidence["heygen"][slot] = {
            "avatarId": avatar_id, "configuredVoiceId": voice_id,
            "defaultVoiceId": default_voice,
        }
        if record is None:
            fails.append(f"heygen {slot}: avatar {avatar_id} not in public catalog")
        elif default_voice != voice_id:
            fails.append(
                f"heygen {slot}: default_voice={default_voice!r} != configured"
                f" {voice_id!r}"
            )
    return fails


def _study_window(args: list[str]) -> tuple[str, str]:
    """--since/--until, else the Anam mint span of the bundle fielded last.

    Chosen by the mint timestamps, not by bundle name: the two published
    bundles sort the opposite way round from the order they were fielded.
    """
    since = until = None
    for i, a in enumerate(args):
        if a == "--since" and i + 1 < len(args):
            since = args[i + 1]
        if a == "--until" and i + 1 < len(args):
            until = args[i + 1]
    if since and until:
        return since, until
    import csv
    spans = []
    for path in (ROOT / "evidence").glob("*/server_manifests.csv"):
        stamps = [row["ts_utc"] for row in csv.DictReader(open(path))
                  if row["provider"] == "anam" and row["ts_utc"]]
        if stamps:
            spans.append((min(stamps), max(stamps)))
    if not spans:
        raise SystemExit(
            "FATAL: no --since/--until and no evidence/*/server_manifests.csv"
            " with Anam mints to derive the study window from"
        )
    return max(spans, key=lambda s: s[1])


def sweep_anam_sessions(env: dict[str, str], providers_cfg: dict,
                        since: str, until: str) -> list[str]:
    key = env.get("ANAM_API_KEY")
    if not key:
        return ["ANAM_API_KEY not set (environment or .env)"]
    headers = {"Authorization": f"Bearer {key}"}
    pinned_llm = providers_cfg["anam"]["llmId"]
    pinned_model = providers_cfg["anam"]["model"]
    # The account also runs non-study sessions. Sessions are narrowed by
    # persona name to the six study slots; the LiveKit study's avatar-only
    # sessions carry the same names, so the window is what separates them.
    study_names = set(json.load(open(ROOT / "config" / "avatars.json"))
                      ["names"].values())

    sessions = _get_all_pages("https://api.anam.ai/v1/sessions", headers)

    def _ts(v: str) -> datetime:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))

    lo, hi = _ts(since), _ts(until)
    in_window = [
        s for s in sessions
        if s.get("startTime") and lo <= _ts(s["startTime"]) <= hi
    ]
    study = [s for s in in_window
             if ((s.get("personaConfig") or {}).get("name")) in study_names]
    skipped = len(in_window) - len(study)
    if not study:
        return [f"anam sweep: no study sessions in window {since}..{until}"
                f" ({len(in_window)} sessions in window, none named"
                f" {sorted(study_names)})"]

    fails: list[str] = []
    rows: list[dict] = []
    llms: dict[str, int] = {}
    versions: dict[str, int] = {}
    for s in study:
        # The list response already embeds the start-time personaConfig
        # snapshot.
        pc = s.get("personaConfig") or {}
        llm = (pc.get("llmConfig") or {})
        llm_id, model_name = llm.get("id"), llm.get("modelName")
        avatar_version = pc.get("avatarVersion")
        rows.append({
            "sessionId": s["id"], "startTime": s.get("startTime"),
            "llmId": llm_id, "llmModelName": model_name,
            "avatarVersion": avatar_version,
        })
        llms[f"{llm_id}/{model_name}"] = llms.get(f"{llm_id}/{model_name}", 0) + 1
        versions[str(avatar_version)] = versions.get(str(avatar_version), 0) + 1
        if llm_id != pinned_llm:
            fails.append(f"anam session {s['id']}: llmId={llm_id!r} != pinned")
        if avatar_version != pinned_model:
            fails.append(
                f"anam session {s['id']}: avatarVersion={avatar_version!r}"
                f" != pinned {pinned_model!r}")

    sweep_evidence = {
        "retrievedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window": {"since": since, "until": until},
        "sessionsInWindow": len(in_window),
        "studySessions": len(study),
        "nonStudySessionsSkipped": skipped,
        "pinned": {"llmId": pinned_llm, "model": pinned_model},
        "distinctLlmConfigs": llms,
        "distinctAvatarVersions": versions,
        "sessions": rows,
    }
    out = EVIDENCE.parent / "anam_session_sweep.json"
    out.write_text(json.dumps(sweep_evidence, indent=2) + "\n")
    print(f"sweep evidence written: {out.relative_to(ROOT)}")
    print(f"anam sweep: {len(study)} study sessions"
          f" ({skipped} non-study skipped) | llmConfigs {llms}"
          f" | avatarVersions {versions}")
    return fails


def main() -> None:
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        raise SystemExit(0)
    # Require both keys before writing anything.
    _env.require("ANAM_API_KEY")
    _env.require("HEYGEN_API_KEY")
    env = os.environ
    cfg = json.load(open(ROOT / "config" / "avatars.json"))
    providers_cfg = json.load(open(ROOT / "config" / "providers.json"))
    evidence: dict = {
        "retrievedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "anam": {}, "heygen": {},
    }
    EVIDENCE.parent.mkdir(exist_ok=True)

    fails = check_anam(env, cfg, providers_cfg, evidence)
    fails += check_anam_voices(env, cfg, evidence)
    fails += check_heygen(env, cfg, evidence)
    EVIDENCE.write_text(json.dumps(evidence, indent=2) + "\n")
    print(f"evidence written: {EVIDENCE.relative_to(ROOT)}")

    if "--sweep-anam-sessions" in sys.argv:
        since, until = _study_window(sys.argv[1:])
        fails += sweep_anam_sessions(env, providers_cfg, since, until)

    if fails:
        for f in fails:
            print(f"FAIL {f}")
        raise SystemExit(1)
    overridden = [s for s, e in evidence["anam"].items()
                  if e.get("pinOverridesActiveVersion")]
    n_av = len(evidence["anam"])
    print(f"PASS anam: pinned model resolves for all {n_av} avatars"
          + (f" ({len(overridden)} pinned above their stored activeVersion:"
             f" {', '.join(sorted(overridden))})"
             if overridden else f" (all {n_av} also carry it as activeVersion)"))
    vs = evidence.get("anam_voices", {})
    if vs:
        kinds = {(v["provider"], v["providerModelId"]) for v in vs.values()}
        print(f"PASS anam voices: all {len(vs)} are stock catalogue voices,"
              f" uniform {sorted(kinds)[0][0]} {sorted(kinds)[0][1]}")
    n_hg = len(evidence.get("heygen", {})) or len(cfg["providers"]["heygen"])
    print(f"PASS heygen: all {n_hg} configured voices == catalog default_voice")
    if "--sweep-anam-sessions" in sys.argv:
        print("PASS anam sweep: every fielded session ran the pinned"
              " llmId + avatarVersion")


if __name__ == "__main__":
    main()
