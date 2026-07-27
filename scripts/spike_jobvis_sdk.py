"""SDK canary: verify the installed ElevenLabs SDK matches what Jobvis expects.

The Python client-tools API has churned before (elevenlabs-python issue #519),
so this canary checks the *installed* SDK before session.py relies on it:

  1. the documented import paths resolve,
  2. ``Conversation`` accepts ``client_tools``,
  3. ``ClientTools.register`` accepts a plain ``fn(parameters: dict)`` handler.

With --live (needs ELEVENLABS_API_KEY + ELEVENLABS_AGENT_ID in .env, a mic, and
free-tier minutes) it runs a real conversation with one dummy tool: ask the
agent "what are my top jobs?" and it should speak the canned Acme/Globex data —
proof that tool calls round-trip. Ctrl-C or "goodbye" ends it.

Run:  uv run python scripts/spike_jobvis_sdk.py [--live]
"""

from __future__ import annotations

import argparse
import inspect
import json
import sys
from importlib.metadata import version


def check_sdk() -> int:
    print(f"elevenlabs version: {version('elevenlabs')}")
    from elevenlabs.client import ElevenLabs  # noqa: F401
    from elevenlabs.conversational_ai.conversation import ClientTools, Conversation
    from elevenlabs.conversational_ai.default_audio_interface import DefaultAudioInterface  # noqa: F401

    print("imports: ok (ElevenLabs, Conversation, ClientTools, DefaultAudioInterface)")

    params = inspect.signature(Conversation.__init__).parameters
    missing = [p for p in ("client_tools", "callback_agent_response", "callback_user_transcript") if p not in params]
    if missing:
        print(f"FAIL: Conversation.__init__ lacks {missing} — session.py needs the WebSocket fallback.")
        return 1
    print("Conversation signature: ok (client_tools + callbacks accepted)")

    tools = ClientTools()
    tools.register("dummy_tool", lambda parameters=None: {"ok": True})
    print("ClientTools.register: ok")
    print("\nSDK matches the plan. Pin this version in pyproject and proceed with session.py.")
    return 0


def run_live() -> int:
    from elevenlabs.client import ElevenLabs
    from elevenlabs.conversational_ai.conversation import ClientTools, Conversation
    from elevenlabs.conversational_ai.default_audio_interface import DefaultAudioInterface

    from job_scout.config import get_settings

    settings = get_settings()
    if not settings.has_voice:
        print("Set ELEVENLABS_API_KEY and ELEVENLABS_AGENT_ID in .env first.", file=sys.stderr)
        return 1

    def get_top_jobs(parameters=None):
        print(f"  << tool call: get_top_jobs({parameters})")
        # NOTE: must be a JSON *string* — the protocol rejects raw dicts (1008).
        return json.dumps(
            {
                "jobs": [
                    {"rank": 1, "title": "ML Engineer", "company": "Acme", "score": 91, "top_gaps": ["Kubernetes"]},
                    {"rank": 2, "title": "Data Scientist", "company": "Globex", "score": 84, "top_gaps": ["Spark"]},
                ],
                "total_ranked": 2,
            }
        )

    tools = ClientTools()
    tools.register("get_top_jobs", get_top_jobs)
    conversation = Conversation(
        client=ElevenLabs(api_key=settings.elevenlabs_api_key.get_secret_value()),
        agent_id=settings.elevenlabs_agent_id,
        requires_auth=True,
        audio_interface=DefaultAudioInterface(),
        client_tools=tools,
        callback_agent_response=lambda text: print(f"agent: {text}"),
        callback_user_transcript=lambda text: print(f"you:   {text}"),
    )
    print("Starting a live session — ask 'what are my top jobs?'; Ctrl-C to stop.")
    conversation.start_session()
    try:
        conversation.wait_for_session_end()
    except KeyboardInterrupt:
        conversation.end_session()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="run a real conversation with one dummy tool (uses minutes)")
    args = parser.parse_args()
    code = check_sdk()
    if code == 0 and args.live:
        code = run_live()
    return code


if __name__ == "__main__":
    sys.exit(main())
