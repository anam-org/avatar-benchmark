#!/usr/bin/env python3
"""Pull per-session rater/device meta for one or more experiments.

Mabyduck records device + demographic meta per rating session
(devicePixelRatio, screenResolution, windowResolution, audio device,
browser/OS, completion time). This pulls it via the API and writes
one JSON file per experiment, plus a summary of the display distribution
(devicePixelRatio, window height percentiles, audio device class).

Usage:
    export MABYDUCK_API_KEY=...   # key with access to the project
    python scripts/pull_session_meta.py --project-id <pid> \
        --experiment-ids <eid1>,<eid2> [--out-dir meta_out]
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from fire import Fire

import mabyduck
import urllib.request

load_dotenv()


def _get(api_key: str, path: str):
    req = urllib.request.Request(
        f"{mabyduck.MABYDUCK_BASE}/{path}",
        headers={"Authorization": f"Api-Key {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _items(payload):
    if isinstance(payload, list):
        return payload
    return payload.get("results", payload.get("items", []))


def _res(v: str | None) -> tuple[int, int] | None:
    try:
        w, h = str(v).split("x")
        return int(w), int(h)
    except Exception:
        return None


def main(
    experiment_ids: str | tuple,
    project_id: str | None = None,
    out_dir: str = "meta_out",
) -> None:
    api_key, project = mabyduck.resolve_credentials(project_id)
    out = Path(out_dir)
    out.mkdir(exist_ok=True)

    all_sessions: list[dict] = []
    ids = (
        [e.strip() for e in experiment_ids.split(",")]
        if isinstance(experiment_ids, str)
        else [str(e).strip() for e in experiment_ids]
    )
    for eid in [e for e in ids if e]:
        sessions = _items(
            _get(api_key, f"projects/{project}/experiments/{eid}/sessions/")
        )
        (out / f"sessions_{eid}.json").write_text(
            json.dumps(sessions, indent=2)
        )
        print(f"{eid}: {len(sessions)} sessions -> {out}/sessions_{eid}.json")
        all_sessions.extend(sessions)

    metas = [s.get("meta") or {} for s in all_sessions]
    dprs = Counter(m.get("devicePixelRatio") for m in metas)
    windows = sorted(
        h for m in metas if (r := _res(m.get("windowResolution"))) for h in [r[1]]
    )
    audio = Counter(
        ("headphone" if any(
            k in str((m.get("audioDevice") or {}).get("name", "")).lower()
            for k in ("headphone", "headset", "earphone", "koptelefoon", "px8", "airpod"))
         else "other/speaker")
        for m in metas
    )
    print(f"\n{len(metas)} sessions total")
    print("devicePixelRatio:", dict(dprs))
    if windows:
        pct = lambda p: windows[int(p / 100 * (len(windows) - 1))]
        print(
            "window height px: "
            f"p5={pct(5)} p10={pct(10)} p25={pct(25)} "
            f"p50={pct(50)} p90={pct(90)}"
        )
    print("audio device:", dict(audio))


if __name__ == "__main__":
    Fire(main)
