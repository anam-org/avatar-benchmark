"""Credential loading for the scripts.

The process environment wins; the repo-root .env fills the gaps. require()
fails with the name of the variable it wanted.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
REPO = Path(__file__).resolve().parents[3]  # repo root: worker, wrangler, node


def load() -> None:
    """Read the repo-root .env. Values already in the environment are kept."""
    load_dotenv(REPO / ".env")


def get(name: str, default: str = "") -> str:
    load()
    return os.environ.get(name) or default


def require(name: str) -> str:
    load()
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"FATAL: {name} not set (environment or .env)")
    return value
