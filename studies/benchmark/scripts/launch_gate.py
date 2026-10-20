#!/usr/bin/env python3
"""Check the dry-run evidence before a launch.

For the newest dry-run session of EVERY provider that has recording chunks
(or an explicit list of session UUIDs), it verifies:
  1. server mint record exists in KV;
  2. client manifest exists, recorder v2+ stats present, video captured,
     >=1 avatar audio stream captured;
  3. recording chunks exist and reassemble into a WebM carrying both a video
     and an audio stream;
  4. BOTH audio channels carry signal (RMS > SILENCE_DB) — L = rater mic,
     R = avatar audio;
  5. conformance at launch time, which differs by variant.

--variant turnkey  conformance is catalog conformance, delegated to
  verify_provider_defaults.py, whose docstring lists what it checks. Mints
  are keyed server-<provider>:<ts>:<uuid>.

--variant livekit  conformance is that every Tavus replica is driven by an
  echo-mode persona, and the shared pipeline is fully specified. Mints are
  keyed server-livekit:<ts>:<uuid> with the provider inside the record, so
  targets are resolved by reading records newest-first.

Usage:
  python scripts/launch_gate.py                        # turnkey, newest per provider
  python scripts/launch_gate.py --variant livekit
  python scripts/launch_gate.py --uuids u1,u2,...      # explicit dry-run set

Requires: wrangler auth (KV reads), ffmpeg/ffprobe on PATH. --variant turnkey
additionally needs ANAM_API_KEY and HEYGEN_API_KEY, which the catalog check it
delegates to reads; --variant livekit needs TAVUS_API_KEY to read persona
modes.
Exit code 0 = gate PASSED for all providers; non-zero otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import _env

ROOT = Path(__file__).resolve().parent.parent
REPO = Path(__file__).resolve().parents[3]  # repo root: worker, wrangler, node
PROVIDERS = ("anam", "tavus", "heygen", "lemonslice")


def _arms() -> tuple[str, ...]:
    """Every arm the LiveKit study can field.

    The turnkey study reaches an arm through its base provider's mint route,
    so its gate speaks in routes. The LiveKit study has one route for all of
    them and records the arm on the mint, so its gate speaks in arms.

    The providers come from config/livekit.json rather than PROVIDERS: the
    two variants do not field the same set."""
    cfg = json.loads((ROOT / "config" / "providers.json").read_text())
    lk = json.loads((ROOT / "config" / "livekit.json").read_text())
    extra = tuple(cfg["anam"].get("arms", {})) + tuple(cfg["tavus"].get("arms", {}))
    return tuple(lk["pagePaths"]) + extra
SILENCE_DB = -60.0  # astats RMS below this = effectively silent channel
MIN_RECORDER_VERSION = 2
# Records read while resolving which provider each server-livekit mint served.
LIVEKIT_SCAN_LIMIT = 200


def kv_namespace_id() -> str:
    """The KV namespace this study writes to, read from the study's own
    wrangler.jsonc — not the repo root's, which belongs to whichever
    study was deployed last."""
    text = (ROOT / "wrangler.jsonc").read_text()
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    for ns in json.loads(text).get("kv_namespaces", []):
        if ns.get("binding") == "MANIFEST":
            return ns["id"]
    raise SystemExit("FATAL: no MANIFEST kv_namespace in wrangler.jsonc")


def _wrangler(*args: str, binary: Path | None = None) -> str | None:
    cmd = ["npx", "wrangler", *args]
    r = subprocess.run(cmd, capture_output=True, cwd=REPO)
    if r.returncode != 0:
        return None
    if binary is not None:
        binary.write_bytes(r.stdout)
        return str(binary)
    return r.stdout.decode()


def _kv_get(key: str, binary: Path | None = None) -> str | None:
    return _wrangler(
        "kv", "key", "get", "--namespace-id", kv_namespace_id(), "--remote", key,
        binary=binary,
    )


def _list_keys() -> list[str]:
    out = _wrangler("kv", "key", "list", "--namespace-id", kv_namespace_id(), "--remote")
    if out is None:
        raise SystemExit("FATAL: cannot list KV keys (wrangler auth?)")
    return [k["name"] for k in json.loads(out)]


def _channel_rms(wav: Path) -> float:
    r = subprocess.run(
        ["ffmpeg", "-i", str(wav), "-af", "astats", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    for line in r.stderr.splitlines():
        if "RMS level dB" in line:
            v = line.rsplit(":", 1)[1].strip()
            return float("-inf") if v == "-inf" else float(v)
    return float("-inf")


def check_session(uuid: str, provider: str, keys: list[str],
                  variant: str = "turnkey") -> list[str]:
    """Return list of failure strings (empty = pass)."""
    fails: list[str] = []

    prefix = "server-livekit:" if variant == "livekit" else f"server-{provider}:"
    mint = [k for k in keys if k.startswith(prefix) and uuid in k]
    if not mint:
        fails.append("no server mint record")

    client_keys = [k for k in keys if k.startswith("client:") and uuid in k]
    if not client_keys:
        fails.append("no client manifest")
    else:
        manifest = json.loads(_kv_get(client_keys[0]) or "{}")
        rec = manifest.get("recorder")
        if not rec:
            fails.append("manifest has no recorder stats (recorder v1?)")
        else:
            if rec.get("version", 0) < MIN_RECORDER_VERSION:
                fails.append(f"recorder version {rec.get('version')} < {MIN_RECORDER_VERSION}")
            if not rec.get("videoCaptured"):
                fails.append("recorder captured NO video track")
            if not rec.get("audioStreamsCaptured"):
                fails.append("recorder captured NO avatar audio stream")

    chunk_keys = sorted(k for k in keys if k.startswith(f"rec:{uuid}:"))
    if not chunk_keys:
        fails.append("no recording chunks")
        return fails

    with tempfile.TemporaryDirectory() as td:
        webm = Path(td) / "session.webm"
        with open(webm, "wb") as out:
            for k in chunk_keys:
                part = Path(td) / "part.bin"
                if _kv_get(k, binary=part) is None:
                    fails.append(f"chunk fetch failed: {k}")
                    return fails
                out.write(part.read_bytes())

        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
             "-of", "json", str(webm)],
            capture_output=True, text=True,
        )
        streams = [s["codec_type"] for s in json.loads(probe.stdout or "{}").get("streams", [])]
        if "video" not in streams:
            fails.append("recording has NO video stream")
        if "audio" not in streams:
            fails.append("recording has NO audio stream")
            return fails

        left = Path(td) / "L.wav"
        right = Path(td) / "R.wav"
        split = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(webm),
             "-filter_complex", "[0:a]channelsplit=channel_layout=stereo[L][R]",
             "-map", "[L]", str(left), "-map", "[R]", str(right)],
            capture_output=True,
        )
        if split.returncode != 0:
            fails.append("audio is not stereo (channel split failed)")
            return fails
        l_rms, r_rms = _channel_rms(left), _channel_rms(right)
        if l_rms < SILENCE_DB:
            fails.append(f"rater-mic channel silent (RMS {l_rms:.1f} dB)")
        if r_rms < SILENCE_DB:
            fails.append(f"AVATAR channel silent (RMS {r_rms:.1f} dB)")

    return fails


def _livekit_mints(keys: list[str], cutoff: int,
                   wanted: tuple[str, ...]) -> dict[str, list[tuple[int, str]]]:
    """provider -> [(ts, uuid)], read from the records.

    LiveKit mints are all keyed server-livekit:<ts>:<uuid>; which provider
    rendered the face is a field inside the record. Records are read
    newest-first and the scan stops as soon as every provider has one.
    """
    entries = sorted(
        ((int(k.split(":")[1]), k.split(":")[2], k)
         for k in keys if k.startswith("server-livekit:")
         and int(k.split(":")[1]) >= cutoff),
        reverse=True)
    # Keyed by the arms this run fields.
    mints: dict[str, list[tuple[int, str]]] = {p: [] for p in wanted}
    for i, (ts, uuid, key) in enumerate(entries):
        if i >= LIVEKIT_SCAN_LIMIT or all(mints[p] for p in wanted):
            break
        rec = json.loads(_kv_get(key) or "{}")
        prov = rec.get("provider")
        if prov in mints:
            mints[prov].append((ts, uuid))
    return mints


def check_livekit_conformance() -> list[str]:
    fails: list[str] = []
    avatars = json.loads((ROOT / "config" / "avatars.json").read_text())
    cfg = json.loads((ROOT / "config" / "livekit.json").read_text())

    pipe = cfg.get("pipeline") or {}
    for stage in ("stt", "llm", "tts"):
        if not (pipe.get(stage) or {}).get("model"):
            fails.append(f"shared pipeline has no {stage} model configured")
    for pool in ("voicePoolFemale", "voicePoolMale"):
        if not (pipe.get("tts") or {}).get(pool):
            fails.append(f"shared pipeline has an empty {pool}")

    providers_cfg = json.loads((ROOT / "config" / "providers.json").read_text())
    # Every Tavus arm.
    replicas = {f"{arm}/{slot}": ids["replicaId"]
                for arm in ("tavus", *providers_cfg["tavus"].get("arms", {}))
                for slot, ids in avatars["providers"][arm].items()}
    personas_path = ROOT / "config" / "livekit_tavus.json"
    if not personas_path.exists():
        fails.append("config/livekit_tavus.json missing — run agent/setup_tavus_personas.py")
        return fails
    personas = json.loads(personas_path.read_text())

    unmapped = sorted(s for s, r in replicas.items() if r not in personas)
    if unmapped:
        fails.append(f"Tavus replicas with no echo persona: {', '.join(unmapped)}")

    key = _env.get("TAVUS_API_KEY")
    if not key:
        fails.append("TAVUS_API_KEY unset — cannot verify personas are echo mode")
        return fails

    for slot, replica in sorted(replicas.items()):
        pid = personas.get(replica)
        if not pid:
            continue
        req = urllib.request.Request(f"https://tavusapi.com/v2/personas/{pid}",
                                     headers={"x-api-key": key})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                p = json.load(r)
        except Exception as e:
            fails.append(f"tavus {slot}: persona {pid} unreadable ({type(e).__name__})")
            continue
        mode = p.get("pipeline_mode")
        if mode != "echo":
            fails.append(f"tavus {slot}: persona {pid} is pipeline_mode={mode!r}, "
                         f"not 'echo' — Tavus would run its own STT/LLM/TTS")
        if p.get("default_replica_id") not in (None, replica):
            fails.append(f"tavus {slot}: persona {pid} points at replica "
                         f"{p.get('default_replica_id')}, not {replica}")
    return fails


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", choices=("turnkey", "livekit"), default="turnkey")
    ap.add_argument("--uuids", default="",
                    help="comma-separated dry-run session UUIDs")
    ap.add_argument("--since-minutes", type=int, default=60,
                    help="ignore sessions older than this; 0 disables the bound")
    ap.add_argument("--providers", default=None,
                    help="comma-separated mint routes a dry-run session is "
                         "required for (default: all four). A run that fields "
                         "only some of them needs only those held by hand.")
    args = ap.parse_args()
    variant = args.variant
    known = _arms() if variant == "livekit" else PROVIDERS
    required = tuple(x.strip() for x in args.providers.split(",")
                     if x.strip()) if args.providers else known
    unknown = [p for p in required if p not in known]
    if unknown:
        raise SystemExit(f"unknown provider(s): {', '.join(unknown)}")
    uuids = [u for u in args.uuids.split(",") if u]
    cutoff = (int(time.time() * 1000) - args.since_minutes * 60_000
              if args.since_minutes else 0)

    if variant == "livekit":
        conformance = check_livekit_conformance()
        for f in conformance:
            print(f"FAIL conformance: {f}")
        defaults_ok = not conformance
        if defaults_ok:
            print("PASS conformance: shared pipeline specified, every Tavus "
                  "replica driven by an echo-mode persona")
    else:
        defaults = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "verify_provider_defaults.py")],
        )
        defaults_ok = defaults.returncode == 0
        if not defaults_ok:
            print("FAIL provider catalog conformance")

    keys = _list_keys()
    if variant == "livekit":
        mints = _livekit_mints(keys, cutoff, required)
    else:
        mints = {p: [] for p in required}
        for k in keys:
            parts = k.split(":")
            if parts[0].startswith("server-") and parts[0] != "server-livekit":
                prov = parts[0][7:]
                if prov in mints and int(parts[1]) >= cutoff:
                    mints[prov].append((int(parts[1]), parts[2]))

    targets: list[tuple[str, str]] = []
    if uuids:
        for u in uuids:
            for prov, entries in mints.items():
                if any(u == e[1] for e in entries):
                    targets.append((prov, u))
    else:
        # Prefer the newest session likely to have recording chunks.
        for prov, entries in mints.items():
            if not entries:
                continue
            chunked = [e for e in entries
                       if any(k.startswith(f"rec:{e[1]}:") for k in keys)]
            targets.append((prov, max(chunked or entries)[1]))

    missing = [p for p in required if p not in {t[0] for t in targets}]
    ok = defaults_ok
    for prov, uuid in sorted(targets):
        fails = check_session(uuid, prov, keys, variant)
        if fails:
            ok = False
            print(f"FAIL {prov:10} {uuid[:8]}:")
            for f in fails:
                print(f"       - {f}")
        else:
            print(f"PASS {prov:10} {uuid[:8]}: mint+manifest+recording verified, both channels live")
    for p in missing:
        ok = False
        window = (f" in the last {args.since_minutes} min"
                  if args.since_minutes else "")
        print(f"FAIL {p:10}: no dry-run session found{window}")

    if not ok:
        print("\nGATE FAILED — do not launch.")
        raise SystemExit(1)
    print("\nGATE PASSED for all providers.")


if __name__ == "__main__":
    main()
