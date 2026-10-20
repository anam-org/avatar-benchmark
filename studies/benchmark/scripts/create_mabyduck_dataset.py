#!/usr/bin/env python3
"""Build and upload the digital-avatar datasets to Mabyduck.

This is step 1 of 3. It creates BOTH datasets an experiment needs:
  - the main dataset  (the pool the scored rounds are drawn from)
  - the training dataset (one group, built with the training prompt)
and uploads each as a Mabyduck dataset, printing both ids. Feed them to
create_mabyduck_experiment.py, then create_mabyduck_job.py.

The building blocks live in two modules next to this file:
- `avatar_dataset` — static config, per-provider builders, baked-in topics, and
  writing/zipping the folder dataset (everything local).
- `mabyduck`       — datasets / experiments / jobs API client (everything
  network).

Usage:
    export MABYDUCK_API_KEY=...
    export MABYDUCK_PROJECT_ID=...
    python scripts/create_mabyduck_dataset.py
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from fire import Fire

load_dotenv()

# Imported after load_dotenv: avatar_dataset reads BENCHMARK_BASE_URL and
# BENCHMARK_URL_SECRET at import time.
import mabyduck  # noqa: E402
from avatar_dataset import (  # noqa: E402
    PROVIDERS,
    PROVIDERS_LIVEKIT,
    build_folder,
    zip_directory,
)

# Datasets are written under here rather than at the current directory.
OUTPUT_ROOT = Path("mabyduck_dataset")


def _build_and_upload(
    *,
    training: bool,
    provider_list: list[str],
    api_key: str,
    project: str,
    variant: str = "main",
) -> str:
    """Build one dataset variant, zip it, upload it, wait until ready, and
    return its Mabyduck dataset id."""
    label = ("training" if training else "rated") + f" [{variant}]"
    suffix = "_training" if training else ""
    prefix = "livekit_" if variant == "livekit" else ""
    out_root = OUTPUT_ROOT / f"{prefix}avatar_files_dataset{suffix}"
    ds_name = (
        f"Avatar evaluation {variant} "
        + ("training dataset" if training else "dataset")
    )

    groups_written, js_written = build_folder(
        out_root=out_root,
        provider_list=provider_list,
        training=training,
        providers_map=PROVIDERS_LIVEKIT if variant == "livekit" else None,
    )
    print(
        f"[{label}] wrote {groups_written} group folder(s) "
        f"and {js_written} provider .js file(s) under {out_root}"
    )

    zip_path = zip_directory(out_root)
    print(f"[{label}] zipped to {zip_path}")

    dataset_id, status_url = mabyduck.upload_dataset(
        api_key=api_key,
        project_id=project,
        name=ds_name,
        zip_path=zip_path,
    )
    mabyduck.wait_until_ready(api_key=api_key, status_url=status_url)
    print(f"[{label}] dataset id: {dataset_id}")
    return dataset_id


def main(
    providers: str | None = None,
    variant: str = "main",
    project_id: str | None = None,
) -> tuple[str, str]:
    """Build + upload the main and training datasets to Mabyduck.

    Topics are baked into `avatar_dataset.TOPICS`. Requires MABYDUCK_API_KEY
    (and MABYDUCK_PROJECT_ID or --project-id).

    Args:
      providers: Comma-separated provider subset (default: all).
      project_id: Mabyduck project ID (overrides MABYDUCK_PROJECT_ID).
    """
    pmap = PROVIDERS_LIVEKIT if variant == "livekit" else PROVIDERS
    if providers is None:
        providers = ",".join(pmap.keys())
    # A comma-separated --providers value arrives from python-fire as a
    # tuple, not a string.
    if isinstance(providers, str):
        provider_list = [p.strip() for p in providers.split(",") if p.strip()]
    else:
        provider_list = [str(p).strip() for p in providers if str(p).strip()]
    for provider in provider_list:
        if provider not in pmap:
            raise SystemExit(f"Unknown provider for variant {variant}: {provider}")

    api_key, project = mabyduck.resolve_credentials(project_id)

    dataset_id = _build_and_upload(
        training=False,
        provider_list=provider_list,
        api_key=api_key,
        project=project,
        variant=variant,
    )
    training_dataset_id = _build_and_upload(
        training=True,
        provider_list=provider_list,
        api_key=api_key,
        project=project,
        variant=variant,
    )

    print("\nUploaded both datasets:")
    print(f"  dataset id:          {dataset_id}")
    print(f"  training dataset id: {training_dataset_id}")
    return dataset_id, training_dataset_id


if __name__ == "__main__":
    Fire(main)
