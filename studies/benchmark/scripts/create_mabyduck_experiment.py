#!/usr/bin/env python3
"""Create a Mabyduck `multi_acr_embed` experiment from existing datasets.

This is step 2 of 3. It needs the two dataset ids that
create_mabyduck_dataset.py prints:
  - the main dataset (the pool the scored rounds are drawn from)
  - the training dataset, used for the practice rounds
Then feed the experiment id this prints to create_mabyduck_job.py.

Usage:
    export MABYDUCK_API_KEY=...
    export MABYDUCK_PROJECT_ID=...

    # 1. build + upload both datasets in one run (prints both ids)
    python scripts/create_mabyduck_dataset.py

    # 2. create the experiment from both
    python scripts/create_mabyduck_experiment.py \
        --dataset-id <dataset-id> --training-dataset-id <training-id>
    python scripts/create_mabyduck_experiment.py \
        --dataset-id <dataset-id> --training-dataset-id <training-id> \
        --name "20Q avatars v3" --language en
"""

from __future__ import annotations

from dotenv import load_dotenv
from fire import Fire

load_dotenv()   # before avatar_dataset, which reads the signing secret at import

import mabyduck  # noqa: E402
from avatar_dataset import (  # noqa: E402
    DEFAULT_CATEGORIES, DEFAULT_IFRAME_SIZE, DEFAULT_INTRODUCTION,
    DEFAULT_QUESTIONS)


def main(
    dataset_id: str,
    training_dataset_id: str,
    name: str = "Digital avatar evaluation",
    language: str = "en",
    project_id: str | None = None,
) -> str:
    """Create a draft `multi_acr_embed` experiment from a dataset.

    Args:
      dataset_id: Id of the main `ready` Mabyduck dataset.
      training_dataset_id: Id of the `ready` training dataset used for the
        practice rounds.
      name: Experiment name.
      language: ISO code for the experiment UI language (default `en`).
      project_id: Mabyduck project ID (overrides MABYDUCK_PROJECT_ID).
    """
    api_key, project = mabyduck.resolve_credentials(project_id)

    experiment = mabyduck.create_experiment(
        api_key=api_key,
        project_id=project,
        name=name,
        dataset_id=dataset_id,
        training_dataset_id=training_dataset_id,
        language=language,
        questions=DEFAULT_QUESTIONS,
        categories=DEFAULT_CATEGORIES,
        iframe_size=DEFAULT_IFRAME_SIZE,
        introduction=DEFAULT_INTRODUCTION,
    )
    print("Created draft experiment:")
    print(f"  id:   {experiment.get('id')}")
    print(f"  name: {experiment.get('name')}")
    print(f"  type: {experiment.get('type')}")
    return experiment.get("id")


if __name__ == "__main__":
    Fire(main)
