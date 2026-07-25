"""Tests for the JobvisSession tool wrapper — the SDK wire boundary.

No ElevenLabs import happens here: the wrapper is pure Python, and the one
contract it must uphold is that results cross the wire as JSON strings — the
ConvAI protocol closes the WebSocket (1008 policy violation) on raw dicts.
"""

from __future__ import annotations

import json

from job_scout.voice.session import EchoGate, JobvisSession


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
