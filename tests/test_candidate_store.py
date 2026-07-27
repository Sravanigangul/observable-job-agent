"""Tests for the persisted candidate and the app's restore-on-load path."""

from __future__ import annotations

import pytest

import job_scout.app as app_module
import job_scout.candidate_store as candidate_store
import job_scout.voice.bridge as bridge_module
from job_scout.voice.bridge import VoiceBridge


@pytest.fixture
def tmp_store(monkeypatch, tmp_path):
    monkeypatch.setattr(candidate_store, "_STORE_DIR", tmp_path)
    monkeypatch.setattr(candidate_store, "_STORE_PATH", tmp_path / "profile.json")
    return candidate_store


def test_save_load_roundtrip(tmp_store, sample_profile):
    tmp_store.save_candidate(sample_profile, "cv text here")
    stored = tmp_store.load_candidate()
    assert stored is not None
    profile, cv_text = stored
    assert profile == sample_profile
    assert cv_text == "cv text here"


def test_load_absent_and_corrupt_return_none(tmp_store, tmp_path):
    assert tmp_store.load_candidate() is None
    (tmp_path / "profile.json").write_text("{not json", encoding="utf-8")
    assert tmp_store.load_candidate() is None
    (tmp_path / "profile.json").write_text('{"version": 1}', encoding="utf-8")
    assert tmp_store.load_candidate() is None  # missing keys


def test_clear_candidate(tmp_store, sample_profile):
    tmp_store.save_candidate(sample_profile, "cv")
    tmp_store.clear_candidate()
    assert tmp_store.load_candidate() is None
    tmp_store.clear_candidate()  # idempotent on an empty store


@pytest.fixture
def fresh_bridge(monkeypatch) -> VoiceBridge:
    bridge = VoiceBridge()
    monkeypatch.setattr(bridge_module, "_BRIDGE", bridge)
    return bridge


def test_on_load_without_store_is_a_noop(tmp_store, fresh_bridge):
    updates = app_module._on_load("t1")
    assert all("visible" not in update for update in updates)
    assert fresh_bridge.snapshot().thread_id == "t1"
    assert fresh_bridge.snapshot().profile is None


def test_on_load_restores_candidate_and_opens_step_two(tmp_store, fresh_bridge, sample_profile):
    tmp_store.save_candidate(sample_profile, "cv text here")

    page_start, page_profile, profile_html, cv_text, profile = app_module._on_load("t1")

    assert page_start["visible"] is False and page_profile["visible"] is True
    assert "Test Candidate" in profile_html and "Restored from your last session" in profile_html
    assert cv_text == "cv text here"
    assert profile == sample_profile
    snap = fresh_bridge.snapshot()
    assert snap.profile == sample_profile and snap.step == "profile"  # Jobvis knows the candidate immediately


def test_reset_forgets_the_stored_candidate(tmp_store, fresh_bridge, sample_profile):
    tmp_store.save_candidate(sample_profile, "cv")
    app_module.reset()
    assert tmp_store.load_candidate() is None
