"""Persist the candidate between app runs: extracted CV text + typed profile.

The two kinds of session state have opposite lifetimes, and the store honors
that split: the CANDIDATE changes rarely, so it survives restarts; JOBS go
stale daily, so search results are deliberately never persisted — every
session fetches fresh (by clicking Find jobs, or by asking Jobvis).

Stored as one JSON file under ``data/candidate/`` (gitignored — it is personal
data). Loading never raises: a missing or corrupt file simply means "no saved
candidate" and the wizard starts from step 1.
"""

from __future__ import annotations

import json
from pathlib import Path

from job_scout.graph.schemas import Profile

_STORE_DIR = Path(__file__).resolve().parents[2] / "data" / "candidate"
_STORE_PATH = _STORE_DIR / "profile.json"


def save_candidate(profile: Profile, cv_text: str) -> None:
    """Persist the extracted candidate, replacing any previous one."""
    _STORE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "profile": profile.model_dump(), "cv_text": cv_text}
    _STORE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_candidate() -> tuple[Profile, str] | None:
    """The stored candidate as ``(profile, cv_text)``, or None (never raises)."""
    try:
        payload = json.loads(_STORE_PATH.read_text(encoding="utf-8"))
        return Profile.model_validate(payload["profile"]), str(payload["cv_text"])
    except (OSError, ValueError, KeyError):
        return None


def clear_candidate() -> None:
    """Forget the stored candidate (the wizard's "start over")."""
    _STORE_PATH.unlink(missing_ok=True)
