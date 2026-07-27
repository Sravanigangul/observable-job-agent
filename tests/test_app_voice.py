"""The app must build with voice on, off, and half-configured — never crash."""

from __future__ import annotations

import job_scout.app as app_module
import job_scout.voice.bridge as bridge_module
from job_scout.runner import RunResult, TailorResult
from job_scout.voice.bridge import VoiceBridge
from tests.conftest import FakeVoiceSession


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

    import job_scout.voice as voice_pkg

    fake_session = FakeVoiceSession(lines=(("you", "tailor the second one"),))
    monkeypatch.setattr(voice_pkg, "get_voice_session", lambda: fake_session)

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

    # the pop also announces itself to the agent, exactly once
    assert len(fake_session.announced) == 1
    assert fake_session.announced[0].startswith("System note:")

    # popped exactly once: the next tick leaves the pages alone
    outputs = app_module.on_voice_tick()
    assert "visible" not in outputs[4]
    assert len(fake_session.announced) == 1


def test_voice_tick_pushes_finished_search_run(monkeypatch, sample_profile):
    fresh = VoiceBridge()
    monkeypatch.setattr(bridge_module, "_BRIDGE", fresh)
    fresh.register_thread("t1")
    fresh.record_profile(sample_profile, "cv text", "t1")

    import job_scout.voice as voice_pkg

    monkeypatch.setattr(voice_pkg, "get_voice_session", lambda: FakeVoiceSession(status="active", level=0.42, latency_ms=380))
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
    assert "--jv-level:0.420" in outputs[0]  # the orb breathes with the voice level
    assert "380 ms" in outputs[0]  # latency HUD


def test_voice_tick_shows_live_progress_while_running(monkeypatch, sample_profile):
    """While a voice-triggered run is in flight, the screen mirrors it — never frozen."""
    import threading
    import time

    fresh = VoiceBridge()
    monkeypatch.setattr(bridge_module, "_BRIDGE", fresh)
    fresh.register_thread("t1")
    fresh.record_profile(sample_profile, "cv text", "t1")

    import job_scout.voice as voice_pkg

    monkeypatch.setattr(voice_pkg, "get_voice_session", lambda: FakeVoiceSession(status="active"))

    release = threading.Event()

    def slow_stream(profile, **kw):
        yield ("status", "ranking 12 jobs…")
        release.wait(timeout=5)
        yield ("result", RunResult())

    monkeypatch.setattr(bridge_module, "stream_search", slow_stream)
    assert fresh.start_search() is None
    for _ in range(200):
        if fresh.run_status().get("latest_status") == "ranking 12 jobs…":
            break
        time.sleep(0.01)

    outputs = app_module.on_voice_tick()
    assert outputs[3]["visible"] is True  # step 3 already shown while the search runs
    assert "ranking 12 jobs…" in outputs[5]  # the runner's live status line, on screen

    release.set()
    for _ in range(200):
        if fresh.run_status()["done"]:
            break
        time.sleep(0.01)
    outputs = app_module.on_voice_tick()
    assert outputs[3]["visible"] is True  # the finished pop still renders the results


def _search_run(ranked_jobs):
    from job_scout.voice.bridge import VoiceRun

    return VoiceRun(kind="search", done=True, search_result=RunResult(ranked_jobs=ranked_jobs))


def test_run_announcements_speak_the_result():
    from job_scout.graph.schemas import RankedJob
    from job_scout.voice.bridge import VoiceRun
    from tests.conftest import make_job

    top = RankedJob(job=make_job("j1", "ML Engineer", "Acme"), fit_score=87, fit_explanation="fits")
    text = app_module._run_announcement(_search_run([top]))
    assert text.startswith("System note:")
    assert "ML Engineer at Acme, 87 out of 100" in text

    assert "found no matching jobs" in app_module._run_announcement(_search_run([]))

    failed = VoiceRun(kind="tailor", done=True, failed=True, error="RateLimitError")
    text = app_module._run_announcement(failed)
    assert "failed" in text and "RateLimitError" in text

    flagged = VoiceRun(kind="tailor", done=True, tailor_result=TailorResult(fabrication_flags=2))
    flagged.tailor_result.pack = object()  # any non-None pack
    text = app_module._run_announcement(flagged)
    assert "2 statements could not be verified" in text
