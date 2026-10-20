#!/usr/bin/env python3
"""Assemble a public evidence bundle for one fielded job.

Runs the whole pipeline:

  1. rating platform  -> ratings.csv, rater_sessions.csv,
                         demographics_aggregate.json
  2. KV evidence      -> session_evidence.csv, server_manifests.csv,
                         recording_chunks.csv
  3. KV recordings    -> audio_envelopes.jsonl.gz (per-channel dBFS at 50 Hz)
  4. derived          -> session_turn_metrics.csv, turn_latencies.csv,
                         ratings_evidence_join.csv
  5. published        -> conditions.csv, study_config.json,
                         evidence_bundle.xlsx

Both study shapes are supported:
  --variant turnkey   server-<provider>:<ts>:<uuid> mints, provider in the key
  --variant livekit   server-livekit:<ts>:<uuid> mints, provider in the record

Rating-platform identifiers are replaced with export-specific codes. Individual
demographic and device details are reduced before publication; calendar
timestamps retain dates only. Aggregate demographics and measured timing remain.
Raw records are assembled in a temporary directory, not the public bundle.

Usage:
  python scripts/build_evidence_bundle.py --experiment x1ecy3l --job j120nhl \
      --variant turnkey --out evidence/x1ecy3l
  python scripts/build_evidence_bundle.py --experiment x11dvlb --job j1zr2wm \
      --variant livekit --out evidence/x11dvlb

  --skip-envelopes reuses an existing audio_envelopes.jsonl.gz in --out.

  --workbook-only --out <dir> rebuilds evidence_bundle.xlsx from the files
  already in <dir>.

The bundle's format is documented in scripts/BUNDLE_FORMAT.md.

Writing into a directory that already holds a bundle requires --force unless
--skip-envelopes is passed.
A recording that fails to decode is fatal; retry first, and pass
--allow-decode-failures only to accept a bundle whose timing figures rest
on fewer sessions than it reports.

Requires MABYDUCK_API_KEY, MABYDUCK_PROJECT_ID, CLOUDFLARE_ACCOUNT_ID
and a temporary BENCHMARK_EXPORT_KEY (32 random bytes or more, hexadecimal),
ffmpeg on PATH, and a wrangler login (its OAuth token is used for the
Cloudflare KV REST API).
"""

from __future__ import annotations

import csv
import gzip
import json
import os
import re
import random
import shutil
import subprocess
import sys
import time
import threading
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import _env
from public_bundle import sanitize_bundle

ROOT = Path(__file__).parent.parent
REPO = Path(__file__).resolve().parents[3]  # repo root: worker, wrangler, node
TURNKEY_PROVIDERS = ("anam", "heygen", "lemonslice", "tavus")
_CONFIG = Path(__file__).parent.parent / "config"
_AVATARS = json.load(open(_CONFIG / "avatars.json"))
_PROVIDERS = json.load(open(_CONFIG / "providers.json"))
PRACTICE_TOPIC = _AVATARS["practiceTopic"]

# The client manifest names only the provider, so the arm is read off the
# mint: the avatar model an Anam arm pins, the replica a Tavus arm casts.
_ARM_BY_MODEL = {
    cfg["avatarModel"]: arm
    for arm, cfg in (_PROVIDERS.get("anam", {}).get("arms") or {}).items()
}
_ARM_BY_REPLICA = {
    av["replicaId"]: arm
    for arm in (_PROVIDERS.get("tavus", {}).get("arms") or {})
    for av in (_AVATARS["providers"].get(arm) or {}).values()
}


def resolve_arm(manifest: dict, provider: str) -> str:
    """The arm a mint belongs to, or the bare provider for a base arm."""
    model = manifest.get("avatarModel")
    if model and model in _ARM_BY_MODEL:
        return _ARM_BY_MODEL[model]
    replica = manifest.get("replicaId")
    if replica and replica in _ARM_BY_REPLICA:
        return _ARM_BY_REPLICA[replica]
    return provider
SR, FRAME = 8000, 160  # 50 Hz envelope
ENV_THRESH, ENV_MIN_DUR, ENV_MERGE = -55.0, 0.30, 0.80


# ----------------------------------------------------------------- plumbing

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


def cf_account() -> str:
    return _env.require("CLOUDFLARE_ACCOUNT_ID")


class KVUnreadable(RuntimeError):
    """KV answered neither the value nor a clean 404."""


# One OAuth token per process; it expires after an hour.
_token = [""]
_token_at = [0.0]
_token_lock = threading.Lock()
_TOKEN_MAX_AGE = 25 * 60


def _read_wrangler_token() -> str:
    # wrangler stores its OAuth token per-platform: macOS under Library,
    # Linux under XDG config, and older versions in the home directory.
    candidates = [
        Path.home() / "Library/Preferences/.wrangler/config/default.toml",
        Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        / ".wrangler/config/default.toml",
        Path.home() / ".wrangler/config/default.toml",
    ]
    cfg = next((c for c in candidates if c.exists()), None)
    if cfg is None:
        raise SystemExit(
            "FATAL: no wrangler config found — run `npx wrangler login`. Looked in:\n  "
            + "\n  ".join(str(c) for c in candidates))
    m = re.search(r'oauth_token\s*=\s*"([^"]+)"', cfg.read_text())
    if not m:
        raise SystemExit("FATAL: no oauth_token in wrangler config")
    return m.group(1)


def cf_token() -> str:
    """CLOUDFLARE_API_TOKEN where it is set, otherwise the wrangler login.

    An API token is what a scheduled run holds and does not expire on a timer;
    the wrangler OAuth token is what a laptop holds and lasts an hour.
    """
    with _token_lock:
        if not _token[0]:
            _token[0] = os.environ.get("CLOUDFLARE_API_TOKEN", "")
            if not _token[0]:
                _token[0] = _read_wrangler_token()
                _token_at[0] = time.time()
        return _token[0]


def refresh_token(older_than: float | None = None) -> str:
    """Replace the token, unless another caller already has.

    wrangler rotates its OAuth token only once the old one has expired, so this
    invokes it and re-reads the config. `older_than` refreshes only a token
    minted before that moment, which is how concurrent readers meeting one
    expiry share a single refresh. An API token is returned unchanged: a 401 on
    one is a permissions problem that refreshing cannot fix.
    """
    if os.environ.get("CLOUDFLARE_API_TOKEN"):
        return _token[0]
    with _token_lock:
        cutoff = time.time() - _TOKEN_MAX_AGE if older_than is None else older_than
        if _token_at[0] > cutoff:
            return _token[0]
        subprocess.run(["npx", "wrangler", "whoami"], capture_output=True, timeout=180)
        _token[0] = _read_wrangler_token()
        _token_at[0] = time.time()
        print("  [wrangler token refreshed]", flush=True)
        return _token[0]


def _kv_url(path: str) -> str:
    return (f"https://api.cloudflare.com/client/v4/accounts/{cf_account()}"
            f"/storage/kv/namespaces/{kv_namespace_id()}/{path}")


def kv_list(prefix: str) -> list[str]:
    names, cursor = [], ""
    while True:
        url = _kv_url(f"keys?limit=1000&prefix={urllib.parse.quote(prefix)}")
        if cursor:
            url += f"&cursor={urllib.parse.quote(cursor)}"
        # Refresh and retry on a 401, as kv_get does.
        body, last = None, None
        for attempt in range(6):
            started = time.time()
            try:
                req = urllib.request.Request(
                    url, headers={"Authorization": f"Bearer {cf_token()}"})
                with urllib.request.urlopen(req, timeout=90) as r:
                    body = json.load(r)
                break
            except urllib.error.HTTPError as err:
                if err.code not in (401, 403):
                    raise
                refresh_token(older_than=started)
                last = err
            except Exception as err:
                last = err
            if attempt == 5:
                which = ("CLOUDFLARE_API_TOKEN"
                         if os.environ.get("CLOUDFLARE_API_TOKEN")
                         else "the wrangler login")
                raise KVUnreadable(
                    f"KV refused {which} with HTTP "
                    f"{getattr(last, 'code', '?')} after a token refresh. An "
                    f"API token needs Workers KV Storage:Read on account "
                    f"{cf_account()}; a wrangler login needs "
                    f"`npx wrangler login`.") from last
            time.sleep(0.5 * 2 ** attempt)
        names += [k["name"] for k in body["result"]]
        cursor = (body.get("result_info") or {}).get("cursor") or ""
        if not cursor:
            return names


def kv_get(key: str) -> bytes | None:
    """The stored value, or None only where the key is genuinely absent.

    Every other outcome raises. A caller cannot tell an unreadable chunk from a
    missing one, so returning None for both writes a truncated recording, or
    drops a session from a bundle, without saying so.
    """
    url = _kv_url(f"values/{urllib.parse.quote(key, safe='')}")
    delay, last = 0.5, None
    for attempt in range(6):
        started = time.time()
        try:
            req = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {cf_token()}"})
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read()
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return None
            if err.code in (401, 403):
                refresh_token(older_than=started)
            last = err
        except Exception as err:
            last = err
        if attempt == 5:
            raise KVUnreadable(f"{key}: {type(last).__name__}: {last}")
        time.sleep(delay + random.random() * delay)
        delay *= 2
    return None


def mabyduck(env: dict[str, str], path: str) -> object:
    url = (f"https://api.mabyduck.com/projects/{env['MABYDUCK_PROJECT_ID']}"
           f"/experiments/{path}")
    req = urllib.request.Request(
        url, headers={"Authorization": f"Api-Key {env['MABYDUCK_API_KEY']}"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)


def strip_prose(value):
    """Drop free-text annotation from a manifest record.

    `note` keys and the prose form of `voiceSelection` are removed at every
    depth.
    """
    if isinstance(value, dict):
        return {k: strip_prose(v) for k, v in value.items()
                if k not in ("note", "voiceSelection")}
    if isinstance(value, list):
        return [strip_prose(v) for v in value]
    return value


def parse_ts(t: str) -> datetime:
    return datetime.fromisoformat(t.replace("Z", "+00:00"))


# --------------------------------------------------------- rating platform

def _slate_pid(slate: dict) -> str:
    """The presentation id the dataset wrapper wrote onto this rating.

    The wrapper calls the platform's setMeta({pid, sessions}). A missing pid
    is not an error: those ratings fall back to the timing/device heuristic
    in build_join().
    """
    for holder in (slate.get("meta"), slate.get("extra_meta"),
                   slate.get("extraMeta"), (slate.get("meta") or {}).get("extra")):
        if isinstance(holder, dict):
            pid = holder.get("pid")
            if isinstance(pid, str) and pid:
                return pid
    return ""


def build_ratings(env, experiment, job, out: Path) -> tuple[list[dict], list[dict]]:
    slates = [s for s in mabyduck(env, f"{experiment}/slates/") if s["job_id"] == job]
    sessions = [s for s in mabyduck(env, f"{experiment}/sessions/")
                if s["job_id"] == job]
    cfg = json.load(open(ROOT / "config" / "experiment.json"))
    dim_col = {q["dimension"]: q["dimension"].lower().replace(" ", "_")
               for q in cfg["questions"]}

    rows = []
    for s in slates:
        by_dim = {r["dimension"]: r for r in s["ratings"]}
        first = s["ratings"][0]
        # "<topic>_<slot>/<condition>.js" — the stimulus path carries both
        stim = first["stimulus"]
        group = stim.split("/")[0]
        topic, _, slot = group.rpartition("_")
        row = dict(
            slate_id=s["id"], is_training=s["is_training"],
            rater_id=s["rater_id"], rater_session_id=s["session_id"],
            date_updated_utc=s["date_updated"], topic=topic, slot=slot,
            provider=first["condition"], stimulus=stim,
            completion_time_ms=(s.get("meta") or {}).get("completionTimeMs"),
            presentation_id=_slate_pid(s),
        )
        for dim, col in dim_col.items():
            row[col] = by_dim[dim]["score"] if dim in by_dim else ""
        rows.append(row)
    rows.sort(key=lambda r: r["date_updated_utc"])
    write_csv(out / "ratings.csv", rows)
    n_pid = sum(1 for r in rows if r["presentation_id"])
    if rows:
        print(f"presentation ids: {n_pid}/{len(rows)} ratings"
              f" ({n_pid / len(rows):.0%})")

    # Temporary raw records for the joins and aggregate counts.
    # sanitize_bundle() reduces these before anything is copied to the output.
    sess_rows = []
    for s in sessions:
        m = s.get("meta") or {}
        d = m.get("demographics") or {}
        sess_rows.append(dict(
            rater_session_id=s["id"], rater_id=(s.get("rater") or {}).get("id"),
            date_created=s["date_created"], date_completed=s.get("date_completed"),
            completion_time_s=m.get("completionTimeSeconds"),
            os_name=(m.get("os") or {}).get("name"),
            os_version=(m.get("os") or {}).get("version"),
            browser=(m.get("browser") or {}).get("name"),
            browser_version=(m.get("browser") or {}).get("version"),
            platform_type=(m.get("platform") or {}).get("type"),
            audio_device=(m.get("audioDevice") or {}).get("name"),
            device_pixel_ratio=m.get("devicePixelRatio"),
            screen_resolution=json.dumps(m.get("screenResolution"))
            if isinstance(m.get("screenResolution"), dict) else m.get("screenResolution"),
            window_resolution=json.dumps(m.get("windowResolution"))
            if isinstance(m.get("windowResolution"), dict) else m.get("windowResolution"),
            client_version=(m.get("versionInfo") or {}).get("tag"),
            age=d.get("age"), sex=d.get("sex"), ethnicity=d.get("ethnicity"),
            employment=d.get("employment"), student=d.get("student"),
            nationality=d.get("nationality"),
            # the platform returns these three in camelCase; accept either
            country_of_birth=d.get("countryOfBirth", d.get("country_of_birth")),
            country_of_residence=d.get("countryOfResidence",
                                       d.get("country_of_residence")),
            primary_language=d.get("primaryLanguage", d.get("primary_language")),
        ))
    sess_rows.sort(key=lambda r: r["date_created"])
    for field in ("age", "sex", "ethnicity", "employment", "nationality",
                  "country_of_birth", "country_of_residence", "primary_language"):
        if not any(r[field] not in (None, "") for r in sess_rows):
            raise SystemExit(
                f"rater_sessions.{field} is empty for all {len(sess_rows)} raters — "
                f"check the demographics key names in the platform response")
    write_csv(out / "rater_sessions.csv", sess_rows)

    agg = {"n_raters": len(sess_rows)}
    for field in ("age", "sex", "ethnicity", "employment", "student",
                  "nationality", "country_of_residence", "primary_language"):
        agg[field] = dict(Counter(
            str(r[field]) for r in sess_rows if r[field] not in (None, "")))
    (out / "demographics_aggregate.json").write_text(json.dumps(agg, indent=2) + "\n")
    print(f"ratings.csv: {len(rows)} | rater_sessions.csv: {len(sess_rows)}")
    return rows, sess_rows


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise SystemExit(f"FATAL: refusing to write empty {path.name}")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow(r)


# ------------------------------------------------------------ KV evidence

def build_evidence(variant, lo, hi, out: Path) -> list[dict]:
    prefix = "server-livekit:" if variant == "livekit" else "server-"
    server_keys = [k for k in kv_list(prefix)
                   if lo <= int(k.split(":")[1]) <= hi]
    if variant == "turnkey":
        server_keys = [k for k in server_keys if not k.startswith("server-livekit")]
    client_keys = [k for k in kv_list("client:")
                   if lo <= int(k.split(":")[1]) <= hi]
    print(f"KV: {len(server_keys)} mints, {len(client_keys)} client manifests")

    with ThreadPoolExecutor(max_workers=16) as ex:
        server_docs = list(ex.map(lambda k: (k, kv_get(k)), server_keys))
        client_docs = list(ex.map(lambda k: (k, kv_get(k)), client_keys))

    server = []
    for key, raw in server_docs:
        if not raw:
            continue
        d = json.loads(raw)
        parts = key.split(":")
        d["_uuid"] = parts[2]
        d["_ts"] = datetime.fromtimestamp(
            int(parts[1]) / 1000, tz=timezone.utc).isoformat()
        d["_provider"] = (d.get("provider") if variant == "livekit"
                          else parts[0].replace("server-", ""))
        server.append(d)

    # The mints decide which manifests belong to this bundle.
    minted = {d["_uuid"] for d in server}
    # Upstream time the provider's own API took to mint the session.
    mint_ms_by_uuid = {d["_uuid"]: d.get("mintUpstreamMs") for d in server}
    arm_by_uuid = {d["_uuid"]: resolve_arm(d, d["_provider"]) for d in server}
    # The edge that served the mint, which is the closest thing the bundle has
    # to where the rater was.
    colo_by_uuid = {d["_uuid"]: d.get("workerColo") for d in server}

    # A session writes its manifest on teardown and the beacon can fire more
    # than once, each landing under its own client:<ts>:<uuid> key. Keyed by
    # session and resolved to the latest, which saw the most of the session:
    # two rows for one session would also put two threads on one filename in
    # build_envelopes.
    latest: dict[str, tuple[int, str, object]] = {}
    for key, raw in client_docs:
        if not raw:
            continue
        parts = key.split(":")
        uuid = parts[2] if len(parts) > 2 else key
        try:
            ts = int(parts[1])
        except (IndexError, ValueError):
            ts = 0
        if uuid not in latest or ts > latest[uuid][0]:
            latest[uuid] = (ts, key, raw)
    dropped = len(client_docs) - len(latest)
    if dropped:
        print(f"  {dropped} superseded client manifest(s) ignored")

    rows = []
    for _ts, key, raw in sorted(latest.values()):
        d = json.loads(raw)
        prov = (d.get("provider") or "").replace("livekit-", "")
        if variant == "turnkey" and prov not in TURNKEY_PROVIDERS:
            continue
        if d.get("sessionUuid") not in minted:
            continue
        meta = d.get("meta") or {}
        samples = {s.get("t"): s for s in d.get("samples", [])}

        def samp(t, field):
            return (samples.get(t) or {}).get(field)

        def last(field):
            if not samples:
                return None
            return (samples[sorted(samples)[-1]] or {}).get(field)

        if meta.get("topic") == PRACTICE_TOPIC:
            continue
        chunks = d.get("recordingChunks") or []
        disp = d.get("display") or {}
        conn = d.get("connectivity") or {}
        events = d.get("events") or []
        rows.append(dict(
            session_uuid=d["sessionUuid"], provider=prov,
            arm=arm_by_uuid.get(d["sessionUuid"]) or prov,
            # Minted by the dataset wrapper and written onto the rating via
            # the platform's setMeta. Empty for any session the wrapper did
            # not mint one for, such as a dry run opened outside the rating
            # platform.
            presentation_id=d.get("presentationId") or "",
            recorded_at_utc=d["recordedAt"],
            duration_seconds=d.get("durationSeconds"), connect_ms=d.get("connectMs"),
            mint_ms=mint_ms_by_uuid.get(d["sessionUuid"]),
            end_state=d.get("endState"), error_detail=d.get("errorDetail"),
            # The stage the session reached, the first-turn readiness path,
            # and which ICE candidate types the network permitted.
            kickoff_via=next((e.get("detail") for e in events
                              if e.get("event") == "kickoff-sent"), ""),
            last_event=(events[-1].get("event") if events else ""),
            ice_candidate_types="+".join(conn.get("candidateTypes") or []),
            ice_srflx=conn.get("srflx"), ice_srflx_ms=conn.get("srflxMs"),
            ice_gather_ms=conn.get("gatherMs"), ice_error=conn.get("error") or "",
            net_effective_type=conn.get("effectiveType"), net_rtt=conn.get("rtt"),
            # Transport actually negotiated.
            net_protocols="+".join(conn.get("protocols") or []),
            net_downlink=conn.get("downlink"),
            worker_colo=colo_by_uuid.get(d["sessionUuid"]) or "",
            # How long the recorder took to find a video track and start, which
            # bounds how much of a short session the recording can cover.
            recorder_start_ms=(d.get("recorder") or {}).get("startDelayMs"),
            slot=meta.get("slot"), topic=meta.get("topic"),
            avatar_or_replica_id=meta.get("avatarId") or meta.get("replicaId") or meta.get("agentId"),
            voice_id=meta.get("voiceId"), model=meta.get("model"),
            # `last` is whichever delivery sample came last in that session.
            res_h_t2=samp(2, "h"), res_h_t15=samp(15, "h"),
            res_h_t60=samp(60, "h"), res_h_last=last("h"),
            res_w_t15=samp(15, "w"), res_w_t60=samp(60, "w"),
            res_w_last=last("w"),
            fps_t15=samp(15, "fps"), fps_t60=samp(60, "fps"),
            fps_last=last("fps"),
            last_sample_t=(sorted(samples)[-1] if samples else None),
            dropped_frames_last=last("droppedFrames"),
            total_frames_last=last("totalFrames"),
            display_w=samp(15, "displayW"), display_h=samp(15, "displayH"),
            viewport_w=disp.get("viewportW"), viewport_h=disp.get("viewportH"),
            device_pixel_ratio=disp.get("devicePixelRatio"),
            user_agent=disp.get("userAgent"),
            recorded=d.get("recorded"), n_recording_chunks=len(chunks),
            recording_bytes=sum(c.get("bytes", 0) for c in chunks),
        ))
    rows.sort(key=lambda r: r["recorded_at_utc"])
    write_csv(out / "session_evidence.csv", rows)

    # Per-session connection milestones, one row per event. This is where a
    # failed session says how far it got: mint, transport, readiness,
    # kickoff, first frame.
    kept_uuids = {r["session_uuid"] for r in rows}
    with open(out / "session_events.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["session_uuid", "provider", "t_ms", "event", "detail"])
        n_ev = 0
        for _, raw in client_docs:
            if not raw:
                continue
            d = json.loads(raw)
            if d.get("sessionUuid") not in kept_uuids:
                continue
            prov = (d.get("provider") or "").replace("livekit-", "")
            for e in d.get("events") or []:
                w.writerow([d["sessionUuid"], prov, e.get("t"),
                            e.get("event"), e.get("detail")])
                n_ev += 1
    if n_ev == 0:
        (out / "session_events.csv").unlink()

    with open(out / "recording_chunks.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["session_uuid", "seq", "at_ms", "bytes"])
        n = 0
        for _, raw in client_docs:
            if not raw:
                continue
            d = json.loads(raw)
            if d.get("sessionUuid") not in kept_uuids:
                continue  # practice / filtered-out session
            for c in d.get("recordingChunks") or []:
                w.writerow([d["sessionUuid"], c.get("seq"), c.get("atMs"), c.get("bytes")])
                n += 1

    # Practice mints are dropped. Non-practice mints with no client evidence
    # are KEPT.
    server = [d for d in server if d.get("topic") != PRACTICE_TOPIC]
    server = [strip_prose(d) for d in server]
    keys = set()
    for d in server:
        keys |= {k for k in d if not k.startswith("_")}
    lead = ["provider", "ts_utc", "session_uuid"]
    # sessionUuid duplicates session_uuid, which comes from the KV key.
    extra = sorted(keys - set(lead) - {"sessionUuid"})
    cols = lead + extra
    with open(out / "server_manifests.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for d in sorted(server, key=lambda d: d["_ts"]):
            w.writerow([d["_provider"], d["_ts"], d["_uuid"]] +
                       [json.dumps(d.get(k)) if isinstance(d.get(k), (dict, list))
                        else d.get(k) for k in extra])
    print(f"session_evidence.csv: {len(rows)} | server_manifests.csv: {len(server)}"
          f" | recording_chunks.csv: {n} | session_events.csv: {n_ev}")
    return rows


# -------------------------------------------------------------- envelopes

def channel_db(webm: Path, ch: str) -> list[int] | None:
    r = subprocess.run(
        ["ffmpeg", "-v", "quiet", "-i", str(webm), "-af", f"pan=mono|c0={ch}",
         "-ar", str(SR), "-f", "s16le", "-"],
        capture_output=True)
    if r.returncode != 0 or not r.stdout:
        return None
    import numpy as np
    a = np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    n = (len(a) // FRAME) * FRAME
    if n == 0:
        return None
    f = a[:n].reshape(-1, FRAME)
    rms = np.sqrt((f ** 2).mean(axis=1) + 1e-12)
    return np.clip(20 * np.log10(rms), -90, 0).astype(np.int16).tolist()


def build_envelopes(evidence: list[dict], out: Path, work: Path,
                    *, allow_decode_failures: bool = False,
                    ) -> None:
    chunk_keys = kv_list("rec:")
    by_session: dict[str, list[str]] = {}
    for k in chunk_keys:
        by_session.setdefault(k.split(":")[1], []).append(k)
    wanted = [e for e in evidence if e["session_uuid"] in by_session]
    print(f"envelopes: {len(wanted)} sessions with recordings")

    read_failed: list[tuple[str, str]] = []
    work.mkdir(parents=True, exist_ok=True)

    def one(e):
        uuid = e["session_uuid"]
        webm = work / f"{uuid}.webm"
        try:
            missing = 0
            try:
                with open(webm, "wb") as fh:
                    for k in sorted(by_session[uuid]):
                        b = kv_get(k)
                        if b:
                            fh.write(b)
                        else:
                            missing += 1
            except KVUnreadable as err:
                # Chunk present, unreadable.
                read_failed.append((uuid, str(err)))
                return {"session_uuid": uuid, "provider": e["provider"],
                        "status": "read_failed"}
            if missing:
                # A short WebM usually still decodes, so an absent chunk
                # would otherwise pass as a complete recording.
                return {"session_uuid": uuid, "provider": e["provider"],
                        "status": "decode_failed"}
            mic, av = channel_db(webm, "c0"), channel_db(webm, "c1")
            if mic is None or av is None:
                return {"session_uuid": uuid, "provider": e["provider"],
                        "status": "decode_failed"}
            return {"session_uuid": uuid, "provider": e["provider"], "status": "ok",
                    "hz": SR // FRAME, "mic_db": mic, "avatar_db": av}
        finally:
            webm.unlink(missing_ok=True)

    done = failed = 0
    # Written beside the target and moved into place only once the run is
    # accepted: these derive from recordings held outside this repository.
    pending = out / "audio_envelopes.jsonl.gz.partial"
    with gzip.open(pending, "wt") as fh:
        with ThreadPoolExecutor(max_workers=12) as ex:
            for rec in ex.map(one, wanted):
                fh.write(json.dumps(rec) + "\n")
                done += 1
                failed += rec["status"] != "ok"
                if done % 100 == 0:
                    print(f"  {done}/{len(wanted)}", flush=True)
    print(f"audio_envelopes.jsonl.gz: {done} ({done - failed} ok, "
          f"{failed - len(read_failed)} decode_failed, {len(read_failed)} read_failed)")
    if done == 0:
        pending.unlink(missing_ok=True)
        raise SystemExit(
            f"FATAL: no recordings found for this window, so there is nothing"
            f"\nto build envelopes from. {out.name} holds this run's ratings, sessions"
            f"\nand manifests and no envelopes, so it is not yet a bundle. Check"
            f"\nthe window and that the recording chunks are still in KV, then"
            f"\nrebuild.")
    if read_failed:
        # Not the same as a recording that did not arrive: these chunks are in
        # KV and this run could not read them, so the sessions are intact and
        # the build is not. --allow-decode-failures does not cover it.
        pending.unlink(missing_ok=True)
        print(f"\nFATAL: {len(read_failed)}/{done} recordings could not be read from"
              f"\nKV. The chunks are present; this run failed to fetch them, so the"
              f"\nsessions are not lost and nothing has been written. Usually an"
              f"\nexpired wrangler login: run `npx wrangler login` and rebuild.",
              flush=True)
        for uuid, why in read_failed[:5]:
            print(f"    {uuid}: {why}")
        raise SystemExit(1)
    if failed and not allow_decode_failures:
        pending.unlink(missing_ok=True)
        print(f"\nFATAL: {failed}/{done} recordings did not decode, so this bundle's"
              f"\ntiming figures would be derived from {done - failed} sessions rather"
              f"\nthan {done}. A failed decode means the recording's chunks did not"
              f"\nall arrive, usually contention rather than absence, so retry before"
              f"\nassuming the data is gone. No envelopes have been written. Pass"
              f"\n--allow-decode-failures to write a bundle whose"
              f"\ntiming figures rest on fewer sessions than it reports.",
              flush=True)
        raise SystemExit(1)
    pending.replace(out / "audio_envelopes.jsonl.gz")
    if failed:
        print(f"\nWARNING: {failed}/{done} recordings did not decode; timing figures"
              f"\nin this bundle rest on {done - failed} sessions, not {done}.",
              flush=True)


# ---------------------------------------------------------------- derived

def env_spans(db, hz):
    import numpy as np
    loud = np.asarray(db, dtype=np.float32) > ENV_THRESH
    if not loud.any():
        return []
    d = np.diff(loud.astype(np.int8))
    starts = list(np.where(d == 1)[0] + 1)
    ends = list(np.where(d == -1)[0] + 1)
    if loud[0]:
        starts.insert(0, 0)
    if loud[-1]:
        ends.append(len(loud))
    return [(s / hz, e / hz) for s, e in zip(starts, ends)
            if (e - s) / hz >= ENV_MIN_DUR]


def env_merge(sp):
    if not sp:
        return []
    out = [list(sp[0])]
    for s, e in sp[1:]:
        if s - out[-1][1] < ENV_MERGE:
            out[-1][1] = e
        else:
            out.append([s, e])
    return [tuple(x) for x in out]


def build_turn_metrics(out: Path) -> None:
    import numpy as np
    if not (out / "audio_envelopes.jsonl.gz").exists():
        print("no audio_envelopes.jsonl.gz — skipping turn metrics")
        return
    # An envelope record names the vendor; the arm it belongs to is resolved
    # on the session row, so read it back from there rather than re-deriving.
    arm_by_uuid: dict[str, str] = {}
    ev_path = out / "session_evidence.csv"
    if ev_path.exists():
        with open(ev_path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                arm_by_uuid[row["session_uuid"]] = row.get("arm") or row.get("provider") or ""

    turn_rows, lat_rows = [], []
    with gzip.open(out / "audio_envelopes.jsonl.gz", "rt") as fh:
        for line in fh:
            e = json.loads(line)
            if e.get("status") != "ok":
                continue
            hz = e["hz"]
            mic = env_merge(env_spans(e["mic_db"], hz))
            av = env_merge(env_spans(e["avatar_db"], hz))
            total = len(e["mic_db"]) / hz
            lat, unanswered = [], 0
            for i, (us, ue) in enumerate(mic):
                if any(a <= ue < b for a, b in av):
                    continue
                nxt = [a for a, _ in av if a > ue + 0.05]
                if not nxt:
                    if ue < total - 3.0:
                        unanswered += 1
                    continue
                a = min(nxt)
                if i + 1 < len(mic) and mic[i + 1][0] < a:
                    continue
                if a - ue >= 0.1:
                    lat.append(round(a - ue, 3))
            for k, g in enumerate(lat):
                lat_rows.append(dict(session_uuid=e["session_uuid"],
                                     provider=e["provider"],
                                     arm=arm_by_uuid.get(e["session_uuid"]) or e["provider"],
                                     turn_index=k, latency_s=g))
            turn_rows.append(dict(
                session_uuid=e["session_uuid"], provider=e["provider"],
                arm=arm_by_uuid.get(e["session_uuid"]) or e["provider"],
                duration_s=round(total, 1), n_user_turns=len(mic),
                n_avatar_turns=len(av),
                user_talk_s=round(sum(b - a for a, b in mic), 1),
                avatar_talk_s=round(sum(b - a for a, b in av), 1),
                n_measured_replies=len(lat), unanswered_turns=unanswered,
                median_latency_s=round(float(np.median(lat)), 3) if lat else ""))
    write_csv(out / "session_turn_metrics.csv", turn_rows)
    write_csv(out / "turn_latencies.csv", lat_rows)
    print(f"session_turn_metrics.csv: {len(turn_rows)} | turn_latencies.csv: {len(lat_rows)}")


def _dpr(v):
    try:
        return round(float(v), 6)
    except (TypeError, ValueError):
        return None


def _session_device(e: dict) -> tuple:
    """The machine a session ran on, read from its own client manifest."""
    ua = e.get("user_agent") or ""
    plat = ("win" if "Windows" in ua else "mac" if "Macintosh" in ua
            else "linux" if "Linux" in ua else "other")
    if "Edg/" in ua:
        br, ver = "edge", ua.split("Edg/")[1].split(" ")[0]
    elif "Firefox/" in ua:
        br, ver = "firefox", ua.split("Firefox/")[1].split(" ")[0]
    elif "Chrome/" in ua:
        br, ver = "chrome", ua.split("Chrome/")[1].split(" ")[0]
    else:
        br, ver = "?", "?"
    return (plat, br, ver, _dpr(e.get("device_pixel_ratio")))


def _rater_device(m: dict) -> tuple:
    """The same tuple, read from the rater's self-reported environment."""
    os_name = m.get("os_name") or ""
    plat = ("win" if "Windows" in os_name else "mac" if "macOS" in os_name
            else "linux" if "Linux" in os_name else "other")
    b = (m.get("browser") or "").lower()
    br = ("edge" if "edge" in b else "firefox" if "firefox" in b
          else "chrome" if "chrome" in b else "?")
    return (plat, br, m.get("browser_version"), _dpr(m.get("device_pixel_ratio")))


def build_join(out: Path) -> None:
    """Match each rating to the session it scored, and say how sure we are.

    Where both sides carry a presentation id the match is exact. Everything
    below it is the fallback for ratings with no presentation id. Each row
    carries a `confidence`:

      exact     the rating and the session carry the same presentation id,
                minted per stimulus presentation by the dataset wrapper.
      unique    exactly one session matched provider + topic + the rater's
                window AND the rater's device fingerprint (DPR + browser +
                version + platform), AND no other rater sharing that device
                overlapped in time on the same provider and topic.
      probable  the rating had exactly one candidate session in its window.
      ambiguous it had more than one; the row reports the latest unassigned
                one.
      none      no candidate left after the already-assigned and 90 s rules;
                session_uuid is empty.
    """
    rs = {r["rater_session_id"]: r for r in csv.DictReader(open(out / "rater_sessions.csv"))}
    ratings = list(csv.DictReader(open(out / "ratings.csv")))
    ev = sorted(csv.DictReader(open(out / "session_evidence.csv")),
                key=lambda e: e["recorded_at_utc"])

    def window(meta):
        return (parse_ts(meta["date_created"]) - timedelta(minutes=2),
                parse_ts(meta["date_completed"] or meta["date_created"])
                + timedelta(minutes=5))

    # Pass 0: for every rating, the candidate sessions and the device-matched
    # subset.
    cand, devmatch = {}, {}
    for r in ratings:
        meta = rs.get(r["rater_session_id"])
        if not meta:
            cand[r["slate_id"]], devmatch[r["slate_id"]] = [], []
            continue
        lo, hi = window(meta)
        # A session carrying a presentation id states which presentation it
        # served. If that is not this rating's, the session is not a candidate
        # for it at any confidence. Sessions with no presentation id stay
        # eligible.
        c = [e for e in ev
             if e["provider"] == r["provider"] and e["topic"] == r["topic"]
             and lo <= parse_ts(e["recorded_at_utc"]) <= hi
             and e["presentation_id"] in ("", r["presentation_id"])]
        want = _rater_device(meta)
        cand[r["slate_id"]] = c
        devmatch[r["slate_id"]] = [e for e in c if _session_device(e) == want]

    # A rating is only forced by the evidence if no OTHER rater could own the
    # same session: same device, an overlapping window, and the same provider
    # and topic.
    rated_pt = {}
    for r in ratings:
        rated_pt.setdefault(r["rater_session_id"], set()).add((r["provider"], r["topic"]))

    def contested(r) -> bool:
        meta = rs.get(r["rater_session_id"])
        if not meta:
            return True
        lo, hi = window(meta)
        want = _rater_device(meta)
        for sid, mm in rs.items():
            if sid == r["rater_session_id"] or _rater_device(mm) != want:
                continue
            l2, h2 = window(mm)
            if h2 < lo or l2 > hi:
                continue
            if (r["provider"], r["topic"]) in rated_pt.get(sid, ()):
                return True
        return False

    assigned, chosen = set(), {}

    # Pass 1a: the presentation id, where both sides have one. A retry
    # produces several sessions under one id; take the last session that
    # reported connected, falling back to the last of them if none did.
    by_pid: dict[str, list[dict]] = {}
    for e in ev:
        pid = e.get("presentation_id") or ""
        if pid:
            by_pid.setdefault(pid, []).append(e)
    for r in ratings:
        pid = r.get("presentation_id") or ""
        group = by_pid.get(pid)
        if not pid or not group:
            continue
        connected = [e for e in group if e.get("end_state") == "completed"]
        pick = (connected or group)[-1]
        assigned.add(pick["session_uuid"])
        chosen[r["slate_id"]] = (pick, "exact")

    # Pass 1b: rows the device fingerprint resolves to exactly one session, with
    # no concurrent twin who could own it.
    for r in ratings:
        if r["slate_id"] in chosen:
            continue
        d = devmatch[r["slate_id"]]
        if len(d) == 1 and d[0]["session_uuid"] not in assigned and not contested(r):
            assigned.add(d[0]["session_uuid"])
            chosen[r["slate_id"]] = (d[0], "unique")

    # Pass 2: everything else falls back to the timing heuristic — the
    # latest unassigned candidate inside the window.
    for r in sorted(ratings, key=lambda r: r["date_updated_utc"]):
        if r["slate_id"] in chosen:
            continue
        tr = parse_ts(r["date_updated_utc"])
        best = None
        for e in cand[r["slate_id"]]:
            if e["session_uuid"] in assigned:
                continue
            if parse_ts(e["recorded_at_utc"]) > tr + timedelta(seconds=90):
                continue
            best = e
        if best:
            assigned.add(best["session_uuid"])
            conf = "probable" if len(cand[r["slate_id"]]) == 1 else "ambiguous"
            chosen[r["slate_id"]] = (best, conf)

    join_rows = []
    for r in ratings:
        hit = chosen.get(r["slate_id"])
        if hit:
            e, conf = hit
            gap = (parse_ts(r["date_updated_utc"])
                   - parse_ts(e["recorded_at_utc"])).total_seconds()
            join_rows.append(dict(slate_id=r["slate_id"],
                                  session_uuid=e["session_uuid"],
                                  gap_s=round(gap, 1), confidence=conf,
                                  n_candidates=len(cand[r["slate_id"]])))
        else:
            join_rows.append(dict(slate_id=r["slate_id"], session_uuid="",
                                  gap_s="", confidence="none",
                                  n_candidates=len(cand[r["slate_id"]])))
    write_csv(out / "ratings_evidence_join.csv", join_rows)
    n = sum(1 for j in join_rows if j["session_uuid"])
    tally = Counter(j["confidence"] for j in join_rows)
    print(f"ratings_evidence_join.csv: {n}/{len(join_rows)} assigned"
          f" ({n / len(join_rows):.0%}) | confidence "
          + ", ".join(f"{k}={tally.get(k, 0)}" for k in
                      ("exact", "unique", "probable", "ambiguous", "none")))


# -------------------------------------------------------------- published

def build_config(variant: str, out: Path) -> None:
    avatars = json.load(open(ROOT / "config" / "avatars.json"))
    exp = json.load(open(ROOT / "config" / "experiment.json"))
    providers = json.load(open(ROOT / "config" / "providers.json"))
    # Only the casts this study fielded. An Anam arm resolves to `anam`;
    # every other arm has its own entry.
    fielded = set()
    ratings_csv = out / "ratings.csv"
    if ratings_csv.exists():
        anam_arms = set(providers["anam"].get("arms", {}))
        with open(ratings_csv, newline="") as fh:
            for r in csv.DictReader(fh):
                if (r.get("is_training") or "").lower() == "true":
                    continue
                arm = r.get("provider") or ""
                fielded.add("anam" if arm in anam_arms else arm)

    # Voice is a per-slot property only in the turnkey study.
    # server_manifests.csv carries the voice each session actually used.
    # mainSlots is the subset a run fields; a slot the config defines but the
    # run did not field is not a condition of this study.
    main = set(avatars.get("mainSlots") or [])
    rows = []
    for prov, slots in avatars["providers"].items():
        if fielded and prov not in fielded:
            continue
        for slot, entry in slots.items():
            if slot == "practice":          # not a scored condition
                continue
            if main and slot not in main:
                continue
            row = dict(
                provider=prov, slot=slot, avatar_name=avatars["names"].get(slot),
                avatar_or_replica_id=entry.get("avatarId") or entry.get("replicaId"))
            if variant == "turnkey":
                row["voice_id"] = entry.get("voiceId", "")
            rows.append(row)
    write_csv(out / "conditions.csv", rows)

    cfg = dict(
        session_cap_seconds=exp.get("sessionCapSeconds"), questions=exp["questions"],
        categories=exp.get("categories"), topics=exp.get("topics"),
        practice_topic=avatars.get("practiceTopic"),
        slot_topics=avatars.get("slotTopics"), anam_model=providers["anam"]["model"],
        introduction=exp.get("introduction"))
    if variant == "livekit":
        cfg["livekit_pipeline"] = strip_prose(
            json.load(open(ROOT / "config" / "livekit.json")))
    (out / "study_config.json").write_text(json.dumps(cfg, indent=2) + "\n")


def build_workbook(out: Path) -> None:
    import openpyxl
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name in ("ratings", "rater_sessions", "session_evidence",
                 "ratings_evidence_join", "session_turn_metrics", "turn_latencies",
                 "session_events", "recording_chunks", "server_manifests",
                 "conditions"):
        p = out / f"{name}.csv"
        if not p.exists():
            continue
        sh = wb.create_sheet(name[:31])
        with open(p) as f:
            for row in csv.reader(f):
                sh.append([(float(x) if re.fullmatch(r"-?\d+(?:\.\d+)?", x)
                            else x) if x != "" else None for x in row])
    ws = wb.create_sheet("study_config")
    ws.append(["key", "value"])
    for k, v in json.load(open(out / "study_config.json")).items():
        ws.append([k, json.dumps(v) if isinstance(v, (dict, list)) else v])
    ws = wb.create_sheet("demographics_agg")
    ws.append(["key", "value"])
    for k, v in json.load(open(out / "demographics_aggregate.json")).items():
        ws.append([k, json.dumps(v)])
    wb.save(out / "evidence_bundle.xlsx")
    print("evidence_bundle.xlsx saved")


def main() -> None:
    args = sys.argv[1:]
    if "--help" in args or "-h" in args:
        print(__doc__)
        raise SystemExit(0)

    def opt(name, default=None):
        return args[args.index(name) + 1] if name in args else default

    experiment, job = opt("--experiment"), opt("--job")
    variant = opt("--variant", "turnkey")
    out = Path(opt("--out", f"evidence/{experiment}"))

    # Rebuild the workbook from what is already on disk: no credentials,
    # no re-fetch.
    if "--workbook-only" in args:
        if not out.exists():
            raise SystemExit(f"FATAL: {out} does not exist")
        build_workbook(out)
        return

    if not experiment or not job:
        raise SystemExit("FATAL: --experiment and --job are required")
    if variant not in ("turnkey", "livekit"):
        raise SystemExit("FATAL: --variant must be turnkey or livekit")

    # --skip-envelopes is exempt: it leaves audio_envelopes.jsonl.gz
    # untouched.
    existing = [p for p in out.glob("*")] if out.exists() else []
    if existing and "--skip-envelopes" not in args and "--force" not in args:
        raise SystemExit(
            f"FATAL: {out} already holds {len(existing)} files.\n"
            f"Rebuilding would overwrite its audio envelopes, which derive from\n"
            f"recordings held outside this repository. Write elsewhere with --out,\n"
            f"regenerate the derived tables only with --skip-envelopes, or pass\n"
            f"--force to overwrite.")
    # Check every credential before writing anything.
    for name in ("MABYDUCK_API_KEY", "MABYDUCK_PROJECT_ID", "CLOUDFLARE_ACCOUNT_ID"):
        _env.require(name)
    if "--skip-envelopes" not in args and shutil.which("ffmpeg") is None:
        raise SystemExit("FATAL: ffmpeg not on PATH — needed to decode recordings")
    kv_namespace_id()
    cf_token()
    # Reuse one temporary export key for related studies so shared raters
    # retain the same public code. It is never included in the output.
    try:
        privacy_key = bytes.fromhex(_env.require("BENCHMARK_EXPORT_KEY"))
    except ValueError:
        raise SystemExit("BENCHMARK_EXPORT_KEY must be hexadecimal")
    if len(privacy_key) < 32:
        raise SystemExit("BENCHMARK_EXPORT_KEY must contain at least 32 random bytes")
    destination = out
    with tempfile.TemporaryDirectory(prefix="avatar-benchmark-raw-") as temporary:
        out = Path(temporary)
        if "--skip-envelopes" in args:
            shutil.copy2(destination / "audio_envelopes.jsonl.gz",
                         out / "audio_envelopes.jsonl.gz")

        env = os.environ
        ratings, sessions = build_ratings(env, experiment, job, out)

        lo = int((parse_ts(min(s["date_created"] for s in sessions))
                  - timedelta(minutes=2)).timestamp() * 1000)
        hi = int((parse_ts(max(s["date_completed"] or s["date_created"] for s in sessions))
                  + timedelta(minutes=30)).timestamp() * 1000)
        print(f"window: {datetime.fromtimestamp(lo/1000, tz=timezone.utc):%Y-%m-%d %H:%M}"
              f" .. {datetime.fromtimestamp(hi/1000, tz=timezone.utc):%Y-%m-%d %H:%M} UTC")

        evidence = build_evidence(variant, lo, hi, out)
        if "--skip-envelopes" not in args:
            build_envelopes(evidence, out, Path("/tmp/bundle_work"),
                            allow_decode_failures="--allow-decode-failures" in args,
                            )
        build_turn_metrics(out)
        build_join(out)
        build_config(variant, out)
        sanitize_bundle(out, privacy_key)
        build_workbook(out)
        destination.mkdir(parents=True, exist_ok=True)
        for item in out.iterdir():
            shutil.copy2(item, destination / item.name)
        print(f"\nbundle written to {destination}")


if __name__ == "__main__":
    main()
