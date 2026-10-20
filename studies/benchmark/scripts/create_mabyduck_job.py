#!/usr/bin/env python3
"""Create and launch a rating job on an existing Mabyduck experiment.

This is step 3 of 3. Get the experiment id from create_mabyduck_experiment.py.
The job is created as a draft and then launched (fetch cost token → confirm),
unless you pass --launch=False to leave it as a draft.

Usage:
    export MABYDUCK_API_KEY=...
    export MABYDUCK_PROJECT_ID=...
    python scripts/create_mabyduck_job.py --experiment-id <experiment-id>
    python scripts/create_mabyduck_job.py \
        --experiment-id <experiment-id> \
        --num-sessions 100 --num-comparisons 10 --rater-pool-id <pool-id>
    # create only, don't launch
    python scripts/create_mabyduck_job.py --experiment-id <experiment-id> --launch=False
"""

from __future__ import annotations


from dotenv import load_dotenv
from fire import Fire

import _env
import mabyduck

load_dotenv()


def main(
    experiment_id: str,
    num_sessions: int = 10,
    num_comparisons: int = 8,
    num_training: int | None = 1,
    rater_pool_id: str | None = None,
    num_golden: int | None = None,
    max_repetitions: int | None = None,
    min_rest_time: int | None = None,
    strategy: str = "randomized",
    note: str | None = None,
    launch: bool = True,
    project_id: str | None = None,
) -> str:
    """Create a rating job under an experiment, then launch it.

    Args:
      experiment_id: Id of the experiment to attach the job to (from
        create_mabyduck_experiment.py).
      num_sessions: Number of rating sessions for the job.
      num_comparisons: Number of comparisons per session.
      rater_pool_id: Rater pool to draw participants from. Defaults to
        MABYDUCK_RATER_POOL_ID.
      num_training: Number of training comparisons per session (optional).
      num_golden: Number of golden/attention-check comparisons (optional).
      max_repetitions: Max times a single item may be shown (optional).
      min_rest_time: Minimum rest time between sessions, seconds (optional).
      strategy: Sampling strategy for the job (default `randomized`).
      note: Free-text note stored on the job (optional).
      launch: Launch the job after creating it (default True). Pass
        --launch=False to leave it as a draft.
      project_id: Mabyduck project ID (overrides MABYDUCK_PROJECT_ID).
    """
    api_key, project = mabyduck.resolve_credentials(project_id)
    # Pool ids are scoped to the experiment, so a static one from the
    # environment only works for the experiment it was taken from. Resolve the
    # named pool on this experiment when none is given, which is what
    # mabyduck.resolve_rater_pool exists for; the env var stays an override.
    rater_pool_id = rater_pool_id or _env.get("MABYDUCK_RATER_POOL_ID") or None
    if not rater_pool_id:
        rater_pool_id = mabyduck.resolve_rater_pool(
            api_key=api_key, project_id=project, experiment_id=experiment_id)
        print(f"  resolved rater pool: {rater_pool_id}")

    job = mabyduck.create_job(
        api_key=api_key,
        project_id=project,
        experiment_id=experiment_id,
        num_sessions=num_sessions,
        num_comparisons=num_comparisons,
        rater_pool_id=rater_pool_id,
        num_training=num_training,
        num_golden=num_golden,
        max_repetitions=max_repetitions,
        min_rest_time=min_rest_time,
        strategy=strategy,
        note=note,
    )
    job_id = job.get("id")
    print("Created job:")
    print(f"  id:       {job_id}")
    print(f"  sessions: {num_sessions}, comparisons: {num_comparisons}")

    if not launch:
        print("Draft job created (not launched). Launch it from the rating\n"
              "platform, or re-run without --launch=False here / without\n"
              "--no-launch via scripts/2_experiment.py.")
        return job_id

    if not job_id:
        raise SystemExit("Job response missing an id — cannot launch")

    launched = mabyduck.launch_job(
        api_key=api_key,
        project_id=project,
        experiment_id=experiment_id,
        job_id=job_id,
    )
    print("Launched job:")
    print(f"  id:     {launched.get('id', job_id)}")
    print(f"  status: {launched.get('status')}")
    return launched.get("id", job_id)


if __name__ == "__main__":
    Fire(main)
