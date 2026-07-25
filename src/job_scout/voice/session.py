"""The Jobvis voice session: one ElevenLabs Agents conversation at a time.

Wraps the SDK ``Conversation`` behind a small state machine (idle → connecting
→ active → idle/error) plus a transcript the Gradio Timer polls. All ElevenLabs
imports happen inside ``start()`` so the rest of the app never needs the
``voice`` extra installed.

If the installed SDK's client-tools API ever diverges from the documented one
(it has churned before — scripts/spike_jobvis_sdk.py is the canary), this module
is the only file to touch: the fallback is to drive the documented ConvAI
WebSocket protocol directly and answer ``client_tool_call`` events by hand.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from array import array
from datetime import datetime

import job_scout.voice.bridge as _bridge
from job_scout.config import get_settings
from job_scout.graph.schemas import Profile
from job_scout.voice.tools import CLIENT_TOOL_HANDLERS

logger = logging.getLogger(__name__)

_TRANSCRIPT_LIMIT = 200


class EchoGate:
    """Half-duplex window: when may the mic pass audio?

    pyaudio has no acoustic echo cancellation, so on open speakers the mic
    hears the agent's own voice — the turn-taking model then transcribes the
    agent as the user and the conversation loops on itself. The gate tracks
    when the queued agent audio finishes playing (16-bit 16 kHz mono PCM) and
    keeps the mic closed until then plus a short hangover for room reverb.
    Trade-off: no barge-in while the agent speaks; headphone users can disable
    the gate (JOBVIS_ECHO_GATE=false) to get interruptions back.
    """

    def __init__(self, hangover_s: float = 0.35, bytes_per_second: int = 2 * 16000) -> None:
        self._hangover_s = hangover_s
        self._bytes_per_second = bytes_per_second
        self._playback_end = float("-inf")  # nothing queued yet: the mic starts open

    def note_output(self, n_bytes: int, now: float) -> None:
        """Extend the closed window by one queued audio chunk."""
        self._playback_end = max(now, self._playback_end) + n_bytes / self._bytes_per_second

    def reset(self) -> None:
        """An interruption flushed the output queue — reopen immediately."""
        self._playback_end = float("-inf")

    def is_open(self, now: float) -> bool:
        return now >= self._playback_end + self._hangover_s


class OutputMeter:
    """Peak level of the agent's voice (0–1) with a short linear fade — drives the UI orb.

    Fed from ``output()`` chunk enqueue times, which track playback closely
    enough for a breathing light; not a real-time VU meter.
    """

    def __init__(self, fade_s: float = 0.7) -> None:
        self._fade_s = fade_s
        self._peak = 0.0
        self._at = float("-inf")

    def note_output(self, audio: bytes, now: float) -> None:
        samples = array("h", audio[: len(audio) - (len(audio) % 2)])
        peak = max((abs(s) for s in samples), default=0) / 32768
        self._peak = max(peak, self.level(now))
        self._at = now

    def level(self, now: float) -> float:
        fade = 1.0 - (now - self._at) / self._fade_s
        return self._peak * fade if fade > 0 else 0.0

    def reset(self) -> None:
        self._peak, self._at = 0.0, float("-inf")


def _greeting_variables(profile: Profile | None, hour: int) -> dict:
    """Dynamic variables for the templated first message (persona.FIRST_MESSAGE)."""
    if 5 <= hour < 12:
        part_of_day = "morning"
    elif 12 <= hour < 18:
        part_of_day = "afternoon"
    else:
        part_of_day = "evening"
    first_name = (profile.name or "").split()[0] if profile is not None and profile.name else ""
    return {"part_of_day": part_of_day, "user_name_suffix": f", {first_name}" if first_name else ""}


class JobvisSession:
    """Lifecycle and observable state of the voice conversation."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status = "idle"  # idle | connecting | active | error
        self._transcript: list[tuple[str, str]] = []
        self._last_error = ""
        self._conversation = None
        self._audio_interface = None
        self._latency_ms: int | None = None

    # ---- observable state (polled by the UI Timer) ---------------------------

    def snapshot(self) -> tuple[str, list[tuple[str, str]], str]:
        with self._lock:
            return self._status, list(self._transcript), self._last_error

    # ---- lifecycle -----------------------------------------------------------

    def start(self) -> tuple[bool, str]:
        """Start a conversation; returns ``(ok, user-facing message)``."""
        with self._lock:
            if self._status in ("connecting", "active"):
                return True, "Jobvis is already listening."
        settings = get_settings()
        if not settings.has_voice:
            return False, "Set ELEVENLABS_API_KEY and ELEVENLABS_AGENT_ID in .env first."
        try:
            from elevenlabs.client import ElevenLabs
            from elevenlabs.conversational_ai.conversation import ClientTools, Conversation, ConversationInitiationData
        except ImportError as exc:
            return False, f"Voice extra not installed ({exc}) — brew install portaudio && uv sync --extra voice."

        client_tools = ClientTools()
        for name, handler in CLIENT_TOOL_HANDLERS.items():
            client_tools.register(name, self._instrument(name, handler))

        self._set("connecting")
        try:
            audio_interface = _build_audio_interface(settings.jobvis_echo_gate)
            greeting = ConversationInitiationData(
                dynamic_variables=_greeting_variables(_bridge.get_bridge().snapshot().profile, datetime.now().hour)
            )
            conversation = Conversation(
                client=ElevenLabs(api_key=settings.elevenlabs_api_key.get_secret_value()),
                agent_id=settings.elevenlabs_agent_id,
                requires_auth=True,
                config=greeting,
                audio_interface=audio_interface,
                client_tools=client_tools,
                callback_agent_response=self._on_agent,
                callback_user_transcript=self._on_user,
                callback_latency_measurement=self._on_latency,
            )
            conversation.start_session()
        except Exception as exc:  # noqa: BLE001 - any SDK/mic failure must surface in the UI, not crash
            self._set("error", f"{type(exc).__name__}: {exc}")
            return False, f"Could not start the voice session: {exc}"

        with self._lock:
            self._conversation = conversation
            self._audio_interface = audio_interface
        threading.Thread(target=self._wait, args=(conversation,), daemon=True, name="jobvis-session").start()
        self._set("active")
        self._log("system", "session started.")
        return True, "Jobvis is listening."

    def stop(self) -> None:
        with self._lock:
            conversation, self._conversation = self._conversation, None
        if conversation is not None:
            try:
                conversation.end_session()
            except Exception as exc:  # noqa: BLE001 - best-effort shutdown
                logger.warning("Jobvis end_session failed: %s", exc)
        self._set("idle")

    # ---- proactivity (the JARVIS part) ---------------------------------------

    def announce(self, text: str) -> bool:
        """Make the agent SPEAK about a background event, unprompted.

        Injects a user-message-typed event (the SDK's send_user_message) — the
        one WS event that triggers a full spoken response; contextual updates
        are silent by design. Returns False when no session is live (the
        screen pop alone carries the news then).
        """
        with self._lock:
            conversation = self._conversation
        if conversation is None:
            return False
        try:
            conversation.send_user_message(text)
        except Exception as exc:  # noqa: BLE001 - a failed announcement must not break the pop
            logger.warning("Jobvis announce failed: %s", exc)
            return False
        self._log("system", f"announced: {text}")
        return True

    def share_context(self, text: str) -> None:
        """Silent situational awareness: colors the agent's next reply, no speech."""
        with self._lock:
            conversation = self._conversation
        if conversation is None:
            return
        try:
            conversation.send_contextual_update(text)
        except Exception as exc:  # noqa: BLE001 - awareness is best-effort
            logger.warning("Jobvis contextual update failed: %s", exc)

    def hud(self) -> dict:
        """Live HUD numbers for the UI: agent voice level (0–1) and last turn latency."""
        with self._lock:
            interface = self._audio_interface
            latency = self._latency_ms
        meter = getattr(interface, "meter", None)
        level = meter.level(time.monotonic()) if meter is not None else 0.0
        return {"level": round(level, 3), "latency_ms": latency}

    # ---- internals -----------------------------------------------------------

    def _wait(self, conversation) -> None:
        """Block on the SDK session thread and reflect how it ended."""
        try:
            conversation.wait_for_session_end()
        except Exception as exc:  # noqa: BLE001 - reflect, never raise on a daemon thread
            self._set("error", f"{type(exc).__name__}: {exc}")
            self._log("system", f"session ended with an error: {exc} (free-tier minutes exhausted?)")
        else:
            self._set("idle")
            self._log("system", "session ended.")
        with self._lock:
            self._conversation = None

    def _on_agent(self, text: str) -> None:
        self._set("active")
        self._log("jobvis", text)

    def _on_user(self, text: str) -> None:
        self._log("you", text)

    def _on_latency(self, ms: int) -> None:
        with self._lock:
            self._latency_ms = int(ms)

    def _set(self, status: str, error: str = "") -> None:
        with self._lock:
            self._status = status
            if error:
                self._last_error = error

    def _log(self, role: str, text: str) -> None:
        if not text:
            return
        with self._lock:
            self._transcript.append((role, text))
            del self._transcript[:-_TRANSCRIPT_LIMIT]

    def _instrument(self, name: str, handler):
        """Wrap a tool: log the call to the transcript, JSON-encode the result.

        The encoding is load-bearing: the ConvAI protocol validates the tool
        result as a STRING, and a raw dict gets the WebSocket closed with a
        1008 policy violation (observed on elevenlabs 2.59.0, despite docs
        saying dicts are fine). Tools keep returning dicts; this is the one
        place they become wire-safe.
        """

        def wrapped(parameters=None):
            args = ", ".join(f"{k}={v!r}" for k, v in (parameters or {}).items() if k != "tool_call_id")
            self._log("tool", f"{name}({args})")
            try:
                return json.dumps(handler(parameters or {}))
            except Exception as exc:  # noqa: BLE001 - a tool bug must not kill the session
                logger.exception("Jobvis tool %s failed", name)
                error = {"error": f"{type(exc).__name__}: {exc}", "note": "Tell the user the tool hit an internal error."}
                return json.dumps(error)

        return wrapped


def _build_audio_interface(gate_echo: bool):
    """The SDK audio interface plus our instrumentation.

    Always subclassed: the OutputMeter feeds the UI orb regardless; the
    half-duplex echo gate is applied unless JOBVIS_ECHO_GATE=false (headphone
    users trade it for barge-in).
    """
    from elevenlabs.conversational_ai.default_audio_interface import DefaultAudioInterface

    class JobvisAudioInterface(DefaultAudioInterface):
        """DefaultAudioInterface with voice-level metering and an optional echo gate."""

        def __init__(self) -> None:
            super().__init__()
            self.meter = OutputMeter()
            self._gate = EchoGate() if gate_echo else None

        def start(self, input_callback):
            def wrapped(audio: bytes) -> None:
                if self._gate is not None and not self._gate.is_open(time.monotonic()):
                    input_callback(b"\x00" * len(audio))  # keep the stream cadence, drop the echo
                else:
                    input_callback(audio)

            super().start(wrapped)

        def output(self, audio: bytes) -> None:
            now = time.monotonic()
            self.meter.note_output(audio, now)
            if self._gate is not None:
                self._gate.note_output(len(audio), now)
            super().output(audio)

        def interrupt(self) -> None:
            self.meter.reset()
            if self._gate is not None:
                self._gate.reset()
            super().interrupt()

    return JobvisAudioInterface()


_SESSION: JobvisSession | None = None
_SESSION_LOCK = threading.Lock()


def get_session() -> JobvisSession:
    """The process-wide session instance (one conversation at a time)."""
    global _SESSION
    with _SESSION_LOCK:
        if _SESSION is None:
            _SESSION = JobvisSession()
        return _SESSION
