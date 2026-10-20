#!/usr/bin/env python3
"""Configure the rating experiment on Mabyduck, and optionally launch it.

Three API steps, chained:

  1  build and upload both datasets — the rated one and the practice one
  2  create the experiment over them
  3  create the rating job, and launch it unless --no-launch

Each step is also runnable on its own (create_mabyduck_dataset.py,
create_mabyduck_experiment.py, create_mabyduck_job.py) when only part of the
chain needs redoing — a second job on an existing experiment, say.

  python scripts/2_experiment.py --variant turnkey --num-sessions 80
  python scripts/2_experiment.py --variant livekit --num-sessions 80
  python scripts/2_experiment.py --variant turnkey --no-launch
  python scripts/2_experiment.py --variant turnkey --experiment-id <id> \
      --num-sessions 20

Stimulus URLs are signed with BENCHMARK_URL_SECRET, so the datasets this
uploads are only usable against a worker holding the same secret. Publish
first (scripts/1_publish.py).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import create_mabyduck_dataset
import create_mabyduck_experiment
import create_mabyduck_job


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", choices=("turnkey", "livekit"), default="turnkey")
    ap.add_argument("--num-sessions", type=int, default=10,
                    help="raters to recruit — this is what you are charged for")
    ap.add_argument("--num-comparisons", type=int, default=8,
                    help="rated conversations per rater")
    ap.add_argument("--num-training", type=int, default=1,
                    help="practice rounds per rater")
    ap.add_argument("--rater-pool-id", default=None,
                    help="default: MABYDUCK_RATER_POOL_ID")
    ap.add_argument("--name", default="Digital avatar evaluation")
    ap.add_argument("--providers", default=None,
                    help="comma-separated subset (default: all in the variant)")
    ap.add_argument("--experiment-id", default=None,
                    help="skip steps 1-2 and add a job to this experiment")
    ap.add_argument("--no-launch", action="store_true",
                    help="leave the job as a draft")
    args = ap.parse_args()

    # create_mabyduck_dataset names the default variant "main"; the two study
    # shapes are turnkey and livekit everywhere else.
    ds_variant = "livekit" if args.variant == "livekit" else "main"
    experiment_id = args.experiment_id

    if experiment_id is None:
        print("=== 1/3  datasets")
        dataset_id, training_dataset_id = create_mabyduck_dataset.main(
            providers=args.providers, variant=ds_variant)

        print("\n=== 2/3  experiment")
        experiment_id = create_mabyduck_experiment.main(
            dataset_id=dataset_id,
            training_dataset_id=training_dataset_id,
            name=args.name)
        if not experiment_id:
            raise SystemExit("experiment creation returned no id")
    else:
        print(f"=== 1-2/3  reusing experiment {experiment_id}")

    print("\n=== 3/3  job")
    job_id = create_mabyduck_job.main(
        experiment_id=experiment_id,
        num_sessions=args.num_sessions,
        num_comparisons=args.num_comparisons,
        num_training=args.num_training,
        rater_pool_id=args.rater_pool_id,
        launch=not args.no_launch)

    print(f"\nexperiment {experiment_id}")
    print(f"job        {job_id}")
    print("\nWhen the job completes, build its bundle:")
    print(f"  python scripts/3_bundle.py --variant {args.variant} "
          f"--experiment {experiment_id} --job {job_id}")


if __name__ == "__main__":
    main()
