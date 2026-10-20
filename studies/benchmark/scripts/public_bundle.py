"""Reduce participant metadata and replace rating-platform IDs before publication.

Run once over all related bundles so shared raters retain the same public code:
    python scripts/public_bundle.py evidence/x1ecy3l evidence/x11dvlb

The random HMAC key is held only in memory and is not written to the bundle.
Workbooks must be rebuilt after this step.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import re
import secrets
import sys
from pathlib import Path

IDS = ("rater_id", "rater_session_id", "slate_id")
RATER_DROP = {
    "ethnicity", "nationality", "country_of_birth", "primary_language",
    "student", "os_version", "browser_version", "screen_resolution",
    "window_resolution", "device_pixel_ratio", "date_created", "date_completed",
}
SESSION_DROP = {
    "user_agent", "device_pixel_ratio", "display_w", "display_h",
    "viewport_w", "viewport_h",
}
DATES = {"date_updated_utc", "recorded_at_utc", "ts_utc", "recordedAt"}
TOKEN = re.compile(r"(?<![0-9])[0-9]{10}\.[0-9a-f]{32}(?![0-9a-f])")
URL = re.compile(r"https?://[^\s<>\"']+")


def age_band(value: str) -> str:
    if not value or value in ("18-29", "30-39", "40-49", "50+"):
        return value
    age = int(value)
    return "18-29" if age < 30 else "30-39" if age < 40 else "40-49" if age < 50 else "50+"


def audio_kind(value: str) -> str:
    return "Headphones" if any(word in value.lower() for word in
        ("headphone", "headset", "earphone", "koptelefoon", "px8", "airpod")) else "Speakers"


def public_id(value: str, field: str, key: bytes) -> str:
    if not value or re.fullmatch(re.escape(field) + r"_[0-9a-f]{64}", value):
        return value
    digest = hmac.new(key, (field + ":" + value).encode(), hashlib.sha256).hexdigest()
    return field + "_" + digest


def scrub_links(value: str) -> str:
    value = URL.sub(lambda match: "[removed session URL]" if
                    re.search(r"[?&](?:amp;)?k=", match.group()) else match.group(), value)
    return TOKEN.sub("[removed session token]", value)


def sanitize_bundle(out: Path, key: bytes) -> None:
    """Retain row order, scores, joins, durations and relative timing measurements."""
    for path in sorted(out.glob("*.csv")):
        with path.open(newline="") as stream:
            reader = csv.DictReader(stream)
            fields, rows = reader.fieldnames, list(reader)
        if fields is None:
            raise ValueError(f"Missing CSV header: {path}")
        drop = RATER_DROP if path.name == "rater_sessions.csv" else (
            SESSION_DROP if path.name == "session_evidence.csv" else set())
        fields = [field for field in fields if field not in drop]
        for row in rows:
            for field in fields:
                value = row[field]
                if field in IDS:
                    value = public_id(value, field, key)
                elif path.name == "rater_sessions.csv" and field == "age":
                    value = age_band(value)
                elif path.name == "rater_sessions.csv" and field == "audio_device":
                    value = audio_kind(value)
                elif field in DATES and value:
                    value = value[:10]
                row[field] = scrub_links(value)
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    print(f"Participant metadata reduced: {out.name}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    key = secrets.token_bytes(32)
    for directory in sys.argv[1:]:
        sanitize_bundle(Path(directory), key)
