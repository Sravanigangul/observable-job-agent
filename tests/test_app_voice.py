"""The app must build with voice on, off, and half-configured — never crash."""

from __future__ import annotations

import job_scout.app as app_module
import job_scout.voice.bridge as bridge_module
from job_scout.runner import RunResult, TailorResult
from job_scout.voice.bridge import VoiceBridge


def test_build_app_without_voice(monkeypatch):
    monkeypatch.setattr(app_module, "is_voice_available", lambda: (False, "Jobvis is off — set ELEVENLABS_API_KEY."))
    assert app_module.build_app() is not None


def test_build_app_with_voice_does_not_import_sdk(monkeypatch):
    """The voice strip builds from availability alone; the SDK loads only on toggle."""
    monkeypatch.setattr(app_module, "is_voice_available", lambda: (True, ""))
    assert app_module.build_app() is not None


def test_voice_tick_pushes_finished_tailor_run(monkeypatch, sample_profile):
    """A finished voice-triggered tailoring pops step 4: pack rendered, pages flipped."""
    fresh = VoiceBridge()
    monkeypatch.setattr(bridge_module, "_BRIDGE", fresh)
    fresh.register_thread("t1")
    fresh.record_profile(sample_profile, "cv text", "t1")

    class FakeSession:
        def snapshot(self):
            return "idle", [("you", "tailor the second one")], ""

    import job_scout.voice as voice_pkg

    monkeypatch.setattr(voice_pkg, "get_voice_session", lambda: FakeSession())

    def run_and_finish(kind: str, result) -> None:
        if kind == "search":
            monkeypatch.setattr(bridge_module, "stream_search", lambda p, **kw: iter([("result", result)]))
            assert fresh.start_search() is None
        else:
            monkeypatch.setattr(bridge_module, "stream_tailor", lambda **kw: iter([("result", result)]))
            assert fresh.start_tailoring("j1") is None
        for _ in range(200):
            if fresh.run_status()["done"]:
                return
            import time

            time.sleep(0.01)
        raise AssertionError("run did not finish")

    run_and_finish("tailor", TailorResult())  # pack=None → renders the error card, still pops step 4
    outputs = app_module.on_voice_tick()
    assert len(outputs) == 12
    page_profile, page_results, page_tailor = outputs[2], outputs[3], outputs[4]
    assert page_tailor["visible"] is True
    assert page_profile["visible"] is False and page_results["visible"] is False

    # popped exactly once: the next tick leaves the pages alone
    outputs = app_module.on_voice_tick()
    assert "visible" not in outputs[4]


def test_voice_tick_pushes_finished_search_run(monkeypatch, sample_profile):
    fresh = VoiceBridge()
    monkeypatch.setattr(bridge_module, "_BRIDGE", fresh)
    fresh.register_thread("t1")
    fresh.record_profile(sample_profile, "cv text", "t1")

    class FakeSession:
        def snapshot(self):
            return "active", [], ""

    import job_scout.voice as voice_pkg

    monkeypatch.setattr(voice_pkg, "get_voice_session", lambda: FakeSession())
    monkeypatch.setattr(bridge_module, "stream_search", lambda p, **kw: iter([("result", RunResult())]))
    assert fresh.start_search() is None
    import time

    for _ in range(200):
        if fresh.run_status()["done"]:
            break
        time.sleep(0.01)

    outputs = app_module.on_voice_tick()
    assert outputs[3]["visible"] is True  # page_results pops
    assert "Jobvis is listening" in outputs[0]
