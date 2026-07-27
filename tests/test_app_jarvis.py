"""Tests for the /jarvis page: standalone session claim, panels, render caching."""

from __future__ import annotations

from pathlib import Path

import pytest

import job_scout.app as app_module
import job_scout.candidate_store as candidate_store
import job_scout.voice.bridge as bridge_module
from job_scout.graph.schemas import CVContent, RankedJob, TailoringPack
from job_scout.renderer import RenderResult
from job_scout.voice.bridge import VoiceBridge
from tests.conftest import FakeVoiceSession, make_job


@pytest.fixture
def fresh_bridge(monkeypatch) -> VoiceBridge:
    bridge = VoiceBridge()
    monkeypatch.setattr(bridge_module, "_BRIDGE", bridge)
    return bridge


@pytest.fixture
def tmp_store(monkeypatch, tmp_path):
    monkeypatch.setattr(candidate_store, "_STORE_DIR", tmp_path)
    monkeypatch.setattr(candidate_store, "_STORE_PATH", tmp_path / "profile.json")
    return candidate_store


def test_ensure_jarvis_session_seeds_saved_candidate(fresh_bridge, tmp_store, sample_profile):
    tmp_store.save_candidate(sample_profile, "cv text")
    app_module._ensure_jarvis_session()
    snap = fresh_bridge.snapshot()
    assert snap.thread_id and snap.profile == sample_profile

    thread_before = snap.thread_id
    app_module._ensure_jarvis_session()  # no-op when a thread exists
    assert fresh_bridge.snapshot().thread_id == thread_before


def test_jarvis_results_panel_states(fresh_bridge, sample_profile):
    empty_snap = fresh_bridge.snapshot()
    html = app_module._jarvis_results_html({}, empty_snap, {"running": False})
    assert "main app" in html  # no candidate → CTA to upload once

    fresh_bridge.register_thread("t1")
    fresh_bridge.record_profile(sample_profile, "cv", "t1")
    html = app_module._jarvis_results_html({}, fresh_bridge.snapshot(), {"running": False})
    assert "Find me jobs" in html

    html = app_module._jarvis_results_html({}, fresh_bridge.snapshot(), {"running": True, "latest_status": "ranking 10 jobs…"})
    assert "ranking 10 jobs…" in html

    ranked = [RankedJob(job=make_job("j1", "ML Engineer", "Acme"), fit_score=87, fit_explanation="fits")]
    html = app_module._jarvis_results_html({"ranked_jobs": ranked}, fresh_bridge.snapshot(), {"running": False})
    assert "ML Engineer" in html and "87" in html


def test_jarvis_pack_panel_renders_once_per_pack(fresh_bridge, monkeypatch):
    calls = []

    def fake_render(cv, name, out_dir):
        calls.append(name)
        return RenderResult(tex_path=Path("/tmp/x.tex"), pdf_path=Path("/tmp/x.pdf"))

    monkeypatch.setattr(app_module, "render_pdf", fake_render)
    monkeypatch.setattr(app_module, "_JARVIS_RENDER_CACHE", {"key": None, "pdf": None, "tex": None})
    pack = TailoringPack(cv=CVContent(headline="DS", summary="s"), cover_letter="Dear team.")
    values = {"tailoring": pack, "fabrication_flags": 0}

    html, pdf_btn, tex_btn = app_module._jarvis_pack_panel(values, fresh_bridge.snapshot())
    assert "Application ready" in html and "no flags" in html
    assert pdf_btn["visible"] is True and tex_btn["visible"] is True
    app_module._jarvis_pack_panel(values, fresh_bridge.snapshot())
    assert len(calls) == 1  # the PDF renders once per pack, not per tick

    none_html, none_pdf, _ = app_module._jarvis_pack_panel({}, fresh_bridge.snapshot())
    assert none_html == "" and none_pdf["visible"] is False


def test_jarvis_tick_returns_all_outputs(fresh_bridge, tmp_store, monkeypatch):
    import job_scout.voice as voice_pkg

    monkeypatch.setattr(voice_pkg, "get_voice_session", lambda: FakeVoiceSession())
    monkeypatch.setattr(bridge_module, "checkpoint_values", lambda thread_id: {})
    outputs = app_module.on_jarvis_tick()
    assert len(outputs) == 7
    assert "standing by" in outputs[0]  # orb idle
    assert outputs[6]["value"] == "Engage Jobvis"
