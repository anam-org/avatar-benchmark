#!/usr/bin/env python3
"""Recompute the study's figures from an evidence bundle.

Standard library only, and reads nothing but the bundle: no API access and
no credentials. This is the step a reader repeats to check a published
number.

  python scripts/4_analyse.py --variant turnkey --bundle evidence/x1ecy3l
  python scripts/4_analyse.py --variant livekit --bundle evidence/x11dvlb
  python scripts/4_analyse.py --variant turnkey --bundle evidence/x1ecy3l \
      --check-envelopes

--variant turnkey runs the within-bundle figures (analysis/reproduce.py).

--variant livekit runs those for the LiveKit bundle and then the cross-study
comparison against the turnkey bundle (analysis/reproduce_livekit.py). Point
--compare-with at the turnkey bundle if it is not the default.

--check-envelopes re-derives turn latency from the raw audio envelopes rather
than reading turn_latencies.csv.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TURNKEY = "evidence/x1ecy3l"


def run(cmd: list[str]) -> None:
    print(f"\n$ {' '.join(cmd)}", flush=True)
    code = subprocess.run(cmd, cwd=ROOT).returncode
    if code != 0:
        raise SystemExit(code)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", choices=("turnkey", "livekit"), default="turnkey")
    ap.add_argument("--bundle", default=None,
                    help="bundle to analyse (default: the variant's published bundle)")
    ap.add_argument("--compare-with", default=DEFAULT_TURNKEY,
                    help="turnkey bundle the LiveKit study is compared against")
    ap.add_argument("--check-envelopes", action="store_true",
                    help="re-derive turn latency from the audio envelopes")
    args = ap.parse_args()

    py = sys.executable
    bundle = args.bundle or (DEFAULT_TURNKEY if args.variant == "turnkey"
                             else "evidence/x11dvlb")

    within = [py, "analysis/reproduce.py", "--bundle", bundle]
    if args.check_envelopes:
        within.append("--check-envelopes")
    run(within)

    if args.variant == "livekit":
        run([py, "analysis/reproduce_livekit.py",
             "--default-stack", args.compare_with, "--normalised-stack", bundle])


if __name__ == "__main__":
    main()
