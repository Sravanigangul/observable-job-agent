"""Tests for the JobvisSession tool wrapper — the SDK wire boundary.

No ElevenLabs import happens here: the wrapper is pure Python, and the one
contract it must uphold is that results cross the wire as JSON strings — the
ConvAI protocol closes the WebSocket (1008 policy violation) on raw dicts.
"""

from __future__ import annotations

import json
from array import array
from unittest.mock import MagicMock

from job_scout.voice.session import EchoGate, JobvisSession, OutputMeter, _greeting_variables


def test_instrumented_tool_returns_json_string_and_filters_call_id():
    session = JobvisSession()
    wrapped = session._instrument("get_top_jobs", lambda parameters: {"jobs": [], "total_ranked": 0})

    result = wrapped({"count": 3, "tool_call_id": "call_abc123"})

    assert isinstance(result, str)
    assert json.loads(result) == {"jobs": [], "total_ranked": 0}
    _, transcript, _ = session.snapshot()
    assert transcript == [("tool", "get_top_jobs(count=3)")]  # tool_call_id noise filtered


def test_instrumented_tool_contains_exceptions_as_json():
    session = JobvisSession()

    def boom(parameters):
        raise ValueError("nope")

    result = json.loads(session._instrument("broken_tool", boom)({}))
    assert "ValueError" in result["error"]
    assert "note" in result


def test_echo_gate_closes_while_agent_audio_plays():
    gate = EchoGate(hangover_s=0.3)
    assert gate.is_open(0.0)
    gate.note_output(32000, now=0.0)  # 1s of 16-bit/16kHz audio → playback ends at t=1.0
    assert not gate.is_open(0.5)
    assert not gate.is_open(1.2)  # still inside the reverb hangover (ends 1.3)
    assert gate.is_open(1.35)


def test_echo_gate_accumulates_chunks_and_restarts_after_idle():
    gate = EchoGate(hangover_s=0.0)
    gate.note_output(16000, now=0.0)  # 0.5s → ends 0.5
    gate.note_output(16000, now=0.1)  # queued behind it → ends 1.0
    assert not gate.is_open(0.9)
    assert gate.is_open(1.0)
    gate.note_output(32000, now=5.0)  # after idle the window starts at 'now', not at the stale end
    assert not gate.is_open(5.9)
    assert gate.is_open(6.0)


def test_echo_gate_reset_reopens_immediately():
    gate = EchoGate(hangover_s=0.3)
    gate.note_output(320000, now=0.0)  # 10s queued
    assert not gate.is_open(5.0)
    gate.reset()  # interruption flushed the output queue
    assert gate.is_open(5.0)


def test_output_meter_peak_and_fade():
    meter = OutputMeter(fade_s=0.7)
    assert meter.level(0.0) == 0.0
    half_loud = array("h", [16384] * 160).tobytes()  # peak 0.5
    meter.note_output(half_loud, now=10.0)
    assert meter.level(10.0) == 0.5
    assert abs(meter.level(10.35) - 0.25) < 1e-9  # halfway through the fade
    assert meter.level(10.8) == 0.0
    meter.note_output(half_loud, now=20.0)
    meter.reset()
    assert meter.level(20.0) == 0.0


def test_greeting_variables(sample_profile):
    assert _greeting_variables(sample_profile, hour=9) == {"part_of_day": "morning", "user_name_suffix": ", Test"}
    assert _greeting_variables(sample_profile, hour=14)["part_of_day"] == "afternoon"
    assert _greeting_variables(sample_profile, hour=22)["part_of_day"] == "evening"
    assert _greeting_variables(None, hour=9) == {"part_of_day": "morning", "user_name_suffix": ""}


def test_announce_speaks_only_on_a_live_session():
    session = JobvisSession()
    assert session.announce("System note: test") is False  # idle: screen pop carries the news alone

    conversation = MagicMock()
    session._conversation = conversation
    assert session.announce("System note: the search finished.") is True
    conversation.send_user_message.assert_called_once_with("System note: the search finished.")
    _, transcript, _ = session.snapshot()
    assert transcript == [("system", "announced: System note: the search finished.")]


def test_share_context_is_silent_and_safe_when_idle():
    session = JobvisSession()
    session.share_context("Screen event: nothing exploded.")  # idle: must not raise

    conversation = MagicMock()
    session._conversation = conversation
    session.share_context("Screen event: CV uploaded.")
    conversation.send_contextual_update.assert_called_once_with("Screen event: CV uploaded.")
    _, transcript, _ = session.snapshot()
    assert transcript == []  # contextual updates are deliberately not transcribed


def test_hud_defaults():
    assert JobvisSession().hud() == {"level": 0.0, "latency_ms": None}
