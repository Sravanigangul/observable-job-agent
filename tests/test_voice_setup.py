"""Tests for the Jobvis agent provisioning payload (scripts/setup_jobvis_agent.py)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from job_scout.voice.persona import FIRST_MESSAGE, JOBVIS_SYSTEM_PROMPT, TOOL_SPECS
from job_scout.voice.tools import CLIENT_TOOL_HANDLERS

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "setup_jobvis_agent.py"
_spec = importlib.util.spec_from_file_location("setup_jobvis_agent", _SCRIPT)
setup_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(setup_script)


def test_agent_payload_carries_persona_and_all_tools():
    payload = setup_script.build_agent_payload(voice_id=None)
    assert payload["name"] == "Jobvis"
    agent = payload["conversation_config"]["agent"]
    assert agent["first_message"] == FIRST_MESSAGE
    assert agent["prompt"]["prompt"] == JOBVIS_SYSTEM_PROMPT
    tools = agent["prompt"]["tools"]
    assert sorted(t["name"] for t in tools) == sorted(CLIENT_TOOL_HANDLERS)
    assert all(t["type"] == "client" for t in tools)
    assert all(t["expects_response"] is True for t in tools)  # wait-for-response: results must reach the agent
    assert "tts" not in payload["conversation_config"]  # no voice id → keep the agent's default


def test_agent_payload_sets_voice_when_given():
    payload = setup_script.build_agent_payload(voice_id="v123")
    assert payload["conversation_config"]["tts"] == {"voice_id": "v123"}


def test_tool_payload_parameter_schema():
    spec = next(s for s in TOOL_SPECS if s["name"] == "get_top_jobs")
    tool = setup_script.build_tool_payload(spec)
    params = tool["parameters"]
    assert params["type"] == "object"
    assert params["properties"]["count"]["type"] == "integer"
    assert params["required"] == []

    spec = next(s for s in TOOL_SPECS if s["name"] == "start_tailoring")
    tool = setup_script.build_tool_payload(spec)
    assert tool["parameters"]["required"] == ["job_ref"]


def test_parameterless_tools_omit_schema():
    spec = next(s for s in TOOL_SPECS if s["name"] == "start_search")
    assert "parameters" not in setup_script.build_tool_payload(spec)
