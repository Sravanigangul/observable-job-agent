"""Jobvis — the voice concierge (ElevenLabs Agents over the Job Scout graph).

Layering rule: ``bridge``, ``tools`` and ``persona`` are pure Python over the
existing graph/runner and never import the ElevenLabs SDK, so they work (and
test) without the ``voice`` extra. Only ``session`` touches the SDK, and only
lazily — the base app must run unchanged when voice is not configured.
"""

from __future__ import annotations

from job_scout.config import get_settings


def is_voice_available() -> tuple[bool, str]:
    """Whether Jobvis can run, and when it cannot, a one-line hint saying why."""
    settings = get_settings()
    if not settings.elevenlabs_api_key.get_secret_value():
        return False, "Jobvis is off — set ELEVENLABS_API_KEY in .env to enable the voice concierge."
    if not settings.elevenlabs_agent_id:
        return False, "Jobvis is off — run `make jobvis-agent` and put the printed ELEVENLABS_AGENT_ID in .env."
    try:
        import elevenlabs  # noqa: F401
        import pyaudio  # noqa: F401
    except ImportError:
        return False, "Jobvis is off — install the voice extra: brew install portaudio && uv sync --extra voice."
    return True, ""


def get_voice_session():
    """The process-wide JobvisSession (imported lazily; needs the voice extra)."""
    from job_scout.voice.session import get_session

    return get_session()
