#!/usr/bin/env python3
"""Build the published evidence bundle for one completed rating job.

Pulls the ratings from Mabyduck and the session evidence from KV, derives
the audio envelopes and turn metrics, and writes the bundle. The bundle is
the unit that gets published: see scripts/BUNDLE_FORMAT.md for what each
file holds.

  python scripts/3_bundle.py --variant turnkey --experiment x1ecy3l --job j120nhl
  python scripts/3_bundle.py --variant livekit --experiment x11dvlb --job j1zr2wm
  python scripts/3_bundle.py --variant turnkey --experiment x1ecy3l --job j120nhl \
      --skip-envelopes

--out defaults to evidence/<experiment>. --skip-envelopes reuses an existing
audio_envelopes.jsonl.gz in the output directory.

Writing into a directory that already holds a bundle needs --force unless
--skip-envelopes is passed, and a
recording that fails to decode is fatal unless --allow-decode-failures is
given.

Needs MABYDUCK_API_KEY, MABYDUCK_PROJECT_ID and CLOUDFLARE_ACCOUNT_ID,
ffmpeg on PATH, and a wrangler login for the KV REST API.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", choices=("turnkey", "livekit"), required=True)
    ap.add_argument("--experiment", required=True)
    ap.add_argument("--job", required=True)
    ap.add_argument("--out", default=None,
                    help="output directory (default: evidence/<experiment>)")
    ap.add_argument("--skip-envelopes", action="store_true",
                    help="reuse the existing audio_envelopes.jsonl.gz in --out")
    ap.add_argument("--force", action="store_true",
                    help="overwrite a directory that already holds a bundle")
    ap.add_argument("--allow-decode-failures", action="store_true",
                    help="write the bundle even if some recordings did not decode")
    args = ap.parse_args()

    out = args.out or f"evidence/{args.experiment}"
    cmd = [sys.executable, "scripts/build_evidence_bundle.py",
           "--experiment", args.experiment,
           "--job", args.job,
           "--variant", args.variant,
           "--out", out]
    if args.skip_envelopes:
        cmd.append("--skip-envelopes")
    if args.force:
        cmd.append("--force")
    if args.allow_decode_failures:
        cmd.append("--allow-decode-failures")

    print(f"$ {' '.join(cmd)}", flush=True)
    code = subprocess.run(cmd, cwd=ROOT).returncode
    if code != 0:
        raise SystemExit(code)

    print(f"\nBundle written to {out}")
    print("Recompute its figures:")
    print(f"  python scripts/4_analyse.py --variant {args.variant} --bundle {out}")


if __name__ == "__main__":
    main()
