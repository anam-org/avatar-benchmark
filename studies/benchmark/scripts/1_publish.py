#!/usr/bin/env python3
"""Publish the rater-facing harness and prove it before a launch.

This runs the whole publish path in order and stops at the first failure:

  1  build the web app and deploy the worker
  2  deploy the LiveKit agent                      (--variant livekit only)
  3  smoke the variant's session-mint routes, signed and unsigned
  4  print signed dry-run URLs, and wait while one session per provider
     is held by hand
  5  launch gate over the evidence those sessions wrote

Step 4 needs a person: the gate reads recordings and both audio channels.
The run pauses there until you press Enter.

The gate's conformance check differs by variant — catalog conformance for
turnkey, echo-mode Tavus personas and a fully specified shared pipeline for
livekit. See scripts/launch_gate.py.

  python scripts/1_publish.py --variant turnkey
  python scripts/1_publish.py --variant livekit
  python scripts/1_publish.py --variant turnkey --skip-deploy
  python scripts/1_publish.py --variant turnkey --gate-only

"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import _env

ROOT = Path(__file__).resolve().parent.parent
REPO = Path(__file__).resolve().parents[3]  # repo root: worker, wrangler, node


def mint_routes(providers: str) -> list[str]:
    """The distinct mint routes a set of arms posts to, in a stable order."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import avatar_dataset as ad

    routes: list[str] = []
    for arm in (p.strip() for p in providers.split(",")):
        if not arm:
            continue
        if arm not in ad.PROVIDERS:
            raise SystemExit(f"unknown arm: {arm}")
        route = ad.PROVIDERS[arm][0].rsplit("/", 1)[-1]
        if route not in routes:
            routes.append(route)
    return routes


def gate_providers(providers: str, livekit: bool) -> str:
    """What the gate needs a dry-run session for.

    The turnkey study reaches an arm through its base provider's mint route,
    and the gate reads mints, so arms collapse to routes. The LiveKit study
    mints every arm through one route and records the arm on the mint, so the
    arms pass through as themselves."""
    if livekit:
        return ",".join(p.strip() for p in providers.split(",") if p.strip())
    return ",".join(mint_routes(providers))


def worker_commit() -> str:
    """The harness revision, baked into the worker at build time and
    recorded on every mint. A dirty tree is suffixed -dirty.
    """
    rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO,
                         capture_output=True, text=True)
    if rev.returncode != 0:
        return "unknown"
    sha = rev.stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=REPO,
                           capture_output=True, text=True)
    return sha + ("-dirty" if dirty.stdout.strip() else "")


def run(label: str, cmd: list[str], cwd: Path = REPO,
        env: dict[str, str] | None = None) -> None:
    """Run one stage, echoing the command, and abort the publish if it fails.

    The default is the repo root, where npm and wrangler belong. A stage that
    invokes one of this study's own scripts passes ROOT, because their paths
    are relative to the study, not the repo."""
    print(f"\n=== {label}\n$ {' '.join(cmd)}", flush=True)
    code = subprocess.run(cmd, cwd=cwd,
                          env={**os.environ, **env} if env else None).returncode
    if code != 0:
        raise SystemExit(f"FAILED: {label} (exit {code})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", choices=("turnkey", "livekit"), default="turnkey")
    ap.add_argument("--skip-deploy", action="store_true",
                    help="the worker is already deployed; start at the smoke test")
    ap.add_argument("--gate-only", action="store_true",
                    help="only re-run the launch gate over existing dry-run sessions")
    ap.add_argument("--base", default=None,
                    help="origin to build dry-run URLs against "
                         "(default: BENCHMARK_BASE_URL)")
    ap.add_argument("--providers", default=None,
                    help="comma-separated arm subset to publish and gate "
                         "(default: all in the variant)")
    args = ap.parse_args()

    py = sys.executable
    livekit = args.variant == "livekit"

    if args.gate_only:
        gate_cmd = [py, "scripts/launch_gate.py", "--variant", args.variant]
        if args.providers:
            gate_cmd += ["--providers", gate_providers(args.providers, livekit)]
        run("launch gate", gate_cmd, cwd=ROOT)
        print("\nGate passed. Launch with scripts/2_experiment.py")
        return

    if not args.skip_deploy:
        # Select this study's worker config: the Astro Cloudflare adapter
        # bakes the root wrangler.jsonc into the build.
        run("select this study's worker config",
            ["cp", str(ROOT / "wrangler.jsonc"), str(REPO / "wrangler.jsonc")])
        commit = worker_commit()
        print(f"  worker commit: {commit}")
        run("build the web app", ["npm", "run", "build"],
            env={"PUBLIC_WORKER_COMMIT": commit})
        run("deploy the worker", ["npx", "wrangler", "deploy"])
        if livekit:
            run("deploy the LiveKit agent", ["bash", "deploy.sh"], cwd=ROOT / "agent")

    base = args.base or _env.get("BENCHMARK_BASE_URL")
    if not base:
        raise SystemExit(
            "FATAL: no --base and no BENCHMARK_BASE_URL set, so the dry-run\n"
            "URLs would point at a dev server that is not running.")
    run("smoke the variant's mint routes",
        [py, "scripts/smoke_worker.py", "--variant", args.variant,
         "--base", base], cwd=ROOT)

    started = time.time()
    urls_cmd = [py, "scripts/local_stimulus_urls.py", "--variant", args.variant,
                "--base", base]
    if args.providers:
        urls_cmd += ["--providers", args.providers]
    run("dry-run stimulus URLs", urls_cmd, cwd=ROOT)

    print("\n=== hold one session per provider now")
    print("Open each URL, allow the microphone, press Start Call, and talk long")
    print("enough to record: the gate needs video, avatar audio, and signal on")
    print("both channels. Then come back here.")
    try:
        input("\nPress Enter once every provider has a dry-run session... ")
    except EOFError:
        print("(non-interactive: continuing straight to the gate)")

    # Bound the gate to sessions held since the URLs were printed.
    window = max(2, int((time.time() - started) / 60) + 1)
    gate_cmd = [py, "scripts/launch_gate.py", "--variant", args.variant,
                "--since-minutes", str(window)]
    if args.providers:
        gate_cmd += ["--providers", gate_providers(args.providers, livekit)]
    run("launch gate", gate_cmd, cwd=ROOT)
    print("\nGate passed. Launch with scripts/2_experiment.py")


if __name__ == "__main__":
    main()
