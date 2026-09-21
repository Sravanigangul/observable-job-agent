"""Unit tests for the Jev job-decision gate."""

from types import SimpleNamespace

import job_scout.graph.nodes.jev_gate as jev_gate_module
from job_scout.graph.schemas import JobPosting, Profile


def make_profile() -> Profile:
    return Profile(
        name="Test Candidate",
        seniority="mid",
        primary_roles=["Healthcare Data Scientist"],
        skills=["Python", "SQL", "machine learning", "clinical data"],
        years_experience=5,
    )


def make_job() -> JobPosting:
    return JobPosting(
        job_id="job-1",
        title="Example Job",
        company="Example Company",
        location="Remote",
        remote=True,
        description="Example job description",
        source="cache",
    )


def make_settings(enabled: bool = True):
    return SimpleNamespace(
        jev_enabled=enabled,
        has_typesafe=True,
        typesafe_api_key=SimpleNamespace(
            get_secret_value=lambda: "test-key"
        ),
    )


def fake_client_for(choice: str, confidence: float):
    """Create a fake TypeSafe client that never calls the network."""

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def system_one(self, *, state, questions):
            answer = SimpleNamespace(
                choice=choice,
                confidence=confidence,
                probabilities={
                    "apply": 0.0,
                    "review": 0.0,
                    "skip": 0.0,
                },
            )
            answer.probabilities[choice] = 1.0

            return SimpleNamespace(
                choices={
                    question_id: answer
                    for question_id in questions
                }
            )

    return FakeClient


def test_disabled_gate_does_not_call_jev(monkeypatch):
    monkeypatch.setattr(
        jev_gate_module,
        "get_settings",
        lambda: make_settings(enabled=False),
    )

    result = jev_gate_module.jev_gate(
        {
            "profile": make_profile(),
            "jobs": [make_job()],
        }
    )

    assert result == {"jev_decisions": []}


def test_high_confidence_skip_removes_job(monkeypatch):
    monkeypatch.setattr(
        jev_gate_module,
        "get_settings",
        make_settings,
    )
    monkeypatch.setattr(
        jev_gate_module,
        "TypeSafeClient",
        fake_client_for("skip", 0.95),
    )

    result = jev_gate_module.jev_gate(
        {
            "profile": make_profile(),
            "jobs": [make_job()],
        }
    )

    assert result["jobs"] == []
    assert result["jev_decisions"][0].raw_choice == "skip"
    assert result["jev_decisions"][0].final_decision == "skip"


def test_low_confidence_skip_is_kept_for_review(monkeypatch):
    job = make_job()

    monkeypatch.setattr(
        jev_gate_module,
        "get_settings",
        make_settings,
    )
    monkeypatch.setattr(
        jev_gate_module,
        "TypeSafeClient",
        fake_client_for("skip", 0.60),
    )

    result = jev_gate_module.jev_gate(
        {
            "profile": make_profile(),
            "jobs": [job],
        }
    )

    assert result["jobs"] == [job]
    assert result["jev_decisions"][0].raw_choice == "skip"
    assert result["jev_decisions"][0].final_decision == "review"


def test_api_failure_preserves_workflow_state(monkeypatch):
    class FailingClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def system_one(self, *, state, questions):
            raise RuntimeError("Simulated API failure")

    monkeypatch.setattr(
        jev_gate_module,
        "get_settings",
        make_settings,
    )
    monkeypatch.setattr(
        jev_gate_module,
        "TypeSafeClient",
        FailingClient,
    )

    result = jev_gate_module.jev_gate(
        {
            "profile": make_profile(),
            "jobs": [make_job()],
            "errors": [],
        }
    )

    assert result["jev_decisions"] == []
    assert "jobs" not in result
    assert result["errors"] == ["Jev gate unavailable: RuntimeError"]

def test_malformed_response_preserves_workflow_state(monkeypatch):
    """Preserve jobs when Jev omits an expected decision."""

    class MalformedClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def system_one(self, *, state, questions):
            return SimpleNamespace(choices={})

    monkeypatch.setattr(
        jev_gate_module,
        "get_settings",
        make_settings,
    )
    monkeypatch.setattr(
        jev_gate_module,
        "TypeSafeClient",
        MalformedClient,
    )

    result = jev_gate_module.jev_gate(
        {
            "profile": make_profile(),
            "jobs": [make_job()],
            "errors": [],
        }
    )

    assert result["jev_decisions"] == []
    assert "jobs" not in result
    assert result["errors"] == ["Jev gate unavailable: KeyError"]
