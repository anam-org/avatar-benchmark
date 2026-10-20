"""Thin Mabyduck API client: datasets, experiments, and jobs.

Study-agnostic: every study-specific value (introduction, categories,
iframe size, questions) is passed in by the caller.

launch_job spends money. It prices the job first and passes the quote to
an optional `confirm` callback, which may raise to abort before anything
is charged.

Docs:
- Datasets:    https://docs.mabyduck.com/api/tutorials/datasets
- Experiments: https://docs.mabyduck.com/api/tutorials/experiments
- Embedded:    https://docs.mabyduck.com/experiments/embedded
"""

from __future__ import annotations

import os
import time
from typing import Callable

import requests

MABYDUCK_BASE = "https://api.mabyduck.com"


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Api-Key {api_key}",
        "Content-Type": "application/json",
    }


def resolve_credentials(project_id: str | None = None) -> tuple[str, str]:
    """Return (api_key, project_id) from args/env, or exit with a message."""
    api_key = os.environ.get("MABYDUCK_API_KEY")
    if not api_key:
        raise SystemExit("MABYDUCK_API_KEY not set")
    resolved_project = project_id or os.environ.get("MABYDUCK_PROJECT_ID")
    if not resolved_project:
        raise SystemExit("MABYDUCK_PROJECT_ID not set (or pass --project-id)")
    return api_key, resolved_project


def upload_dataset(
    *, api_key: str, project_id: str, name: str, zip_path: Path
) -> tuple[str, str]:
    """Create the dataset entry, PUT the zip, return (dataset_id, status_url)."""
    create_url = f"{MABYDUCK_BASE}/projects/{project_id}/datasets/"
    print(f"POST {create_url}")
    res = requests.post(
        create_url,
        headers=_headers(api_key),
        json={"name": name, "filename": zip_path.name},
        timeout=30,
    )
    res.raise_for_status()
    payload = res.json()
    dataset_id: str = payload["id"]
    upload_url: str = payload["upload_url"]
    status_url: str = payload["status_url"]

    print(f"PUT {upload_url} ({zip_path.stat().st_size:,} bytes)")
    with zip_path.open("rb") as f:
        put = requests.put(upload_url, data=f, timeout=300)
    put.raise_for_status()

    return dataset_id, status_url


def wait_until_ready(
    *, api_key: str, status_url: str, timeout_s: int = 600, poll_s: int = 5
) -> None:
    print(f"Polling {status_url} …")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        res = requests.get(
            status_url,
            headers={"Authorization": f"Api-Key {api_key}"},
            timeout=30,
        )
        # Retry a 5xx rather than failing the run.
        if res.status_code >= 500:
            print(f"  server {res.status_code}, retrying in {poll_s}s")
            time.sleep(poll_s)
            continue
        res.raise_for_status()
        data = res.json()
        status = data.get("status")
        if status == "ready":
            print("  → ready")
            return
        if status == "error":
            raise SystemExit(
                f"Mabyduck reported dataset processing error: {data.get('error')}"
            )
        print(f"  status={status}, waiting {poll_s}s")
        time.sleep(poll_s)
    raise SystemExit(f"Timed out waiting for dataset (>{timeout_s}s)")


def create_experiment(
    *,
    api_key: str,
    project_id: str,
    name: str,
    dataset_id: str,
    training_dataset_id: str | None = None,
    language: str,
    introduction: str = "",
    questions: list[dict],
    categories: list[dict],
    iframe_size: dict | None = None,
    experiment_type: str = "multi_acr_embed",
    question_header: str = "How much do you agree with the following statements?",
    title: str = "Digital avatar evaluation - 20 Questions",
    description: str = (
        "Continue talking to the avatar until the game ends or the avatar "
        "stops the conversation (up to 2 minutes). Afterward, evaluate your "
        "interactions below."
    ),
) -> dict:
    url = f"{MABYDUCK_BASE}/projects/{project_id}/experiments/"
    # multi_acr rates uploaded video files; multi_acr_embed rates the
    # harness pages in an iframe. Each type reads its own config block.
    type_config = {
        "questions": questions,
        "categories": categories,
        "hasAudio": True,
    }
    if experiment_type == "multi_acr_embed" and iframe_size:
        type_config["iframeSize"] = iframe_size
    config_key = (
        "multiAcrEmbed" if experiment_type == "multi_acr_embed" else "multiAcr"
    )
    body = {
        "name": name,
        "type": experiment_type,
        "language": language,
        "datasets": [dataset_id],
        "training_datasets": ([training_dataset_id] if training_dataset_id else []),
        "title": title,
        "question": question_header,
        "introduction": introduction,
        "description": description,
        "config": {config_key: type_config},
    }
    print(f"POST {url}")
    res = requests.post(url, headers=_headers(api_key), json=body, timeout=30)
    if res.status_code >= 400:
        raise SystemExit(
            f"Mabyduck experiment creation failed ({res.status_code}): {res.text}"
        )
    return res.json()


def resolve_rater_pool(
    *,
    api_key: str,
    project_id: str,
    experiment_id: str,
    name: str = "Crowd-sourced raters",
) -> str:
    """Return the experiment's rater-pool id for the given pool name.

    Pool ids are scoped to the experiment. When several
    pools share the name, the unlabeled one wins.
    """
    url = (
        f"{MABYDUCK_BASE}/projects/{project_id}"
        f"/experiments/{experiment_id}/rater_pools/"
    )
    res = requests.get(url, headers=_headers(api_key), timeout=30)
    res.raise_for_status()
    pools = [p for p in res.json() if p.get("name") == name]
    if not pools:
        raise SystemExit(f"no rater pool named {name!r} on {experiment_id}")
    pools.sort(key=lambda p: p.get("label") or "")
    return pools[0]["id"]


def create_job(
    *,
    api_key: str,
    project_id: str,
    experiment_id: str,
    num_sessions: int,
    num_comparisons: int,
    rater_pool_id: str,
    num_training: int | None = None,
    num_golden: int | None = None,
    max_repetitions: int | None = None,
    min_rest_time: int | None = None,
    strategy: str = "randomized",
    note: str | None = None,
) -> dict:
    """Create a rating job under an experiment."""
    url = f"{MABYDUCK_BASE}/projects/{project_id}/experiments/{experiment_id}/jobs/"
    body: dict = {
        "num_sessions": num_sessions,
        "num_comparisons": num_comparisons,
        "strategy": strategy,
    }
    if rater_pool_id is not None:
        body["rater_pool_id"] = rater_pool_id
    if num_training is not None:
        body["num_training"] = num_training
    if num_golden is not None:
        body["num_golden"] = num_golden
    if max_repetitions is not None:
        body["max_repetitions"] = max_repetitions
    if min_rest_time is not None:
        body["min_rest_time"] = min_rest_time
    if note is not None:
        body["note"] = note

    print(f"POST {url}")
    res = requests.post(url, headers=_headers(api_key), json=body, timeout=30)
    if res.status_code >= 400:
        raise SystemExit(
            f"Mabyduck job creation failed ({res.status_code}): {res.text}"
        )
    return res.json()


def launch_job(
    *,
    api_key: str,
    project_id: str,
    experiment_id: str,
    job_id: str,
    confirm: Callable[[float, str], None] | None = None,
) -> dict:
    """Launch a created (draft) job. Two steps: fetch the cost breakdown to
    obtain a confirmation token, then POST that token to launch."""
    base = (
        f"{MABYDUCK_BASE}/projects/{project_id}"
        f"/experiments/{experiment_id}/jobs/{job_id}"
    )

    costs_url = f"{base}/costs/"
    print(f"GET {costs_url}")
    res = requests.get(costs_url, headers=_headers(api_key), timeout=30)
    if res.status_code >= 400:
        raise SystemExit(
            f"Mabyduck job costs failed ({res.status_code}): {res.text}"
        )
    costs = res.json()
    token = costs.get("token")
    if not token:
        raise SystemExit(f"No launch token in job costs response: {costs}")
    cost = float(costs.get("cost"))
    currency = str(costs.get("currency") or "")
    print(f"  cost: {cost} {currency} for {costs.get('num_sessions')} session(s)")
    if confirm is not None:
        confirm(cost, currency)

    launch_url = f"{base}/launch/"
    print(f"POST {launch_url}")
    res = requests.post(
        launch_url, headers=_headers(api_key), json={"token": token}, timeout=30
    )
    if res.status_code >= 400:
        raise SystemExit(
            f"Mabyduck job launch failed ({res.status_code}): {res.text}"
        )
    return res.json()
