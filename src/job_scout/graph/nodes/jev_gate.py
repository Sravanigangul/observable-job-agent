"""Use Jev to route jobs before detailed LLM ranking."""

from __future__ import annotations

from typesafe_sdk import Choice, TypeSafeClient

from job_scout.config import get_settings
from job_scout.graph.schemas import JevJobDecision
from job_scout.graph.state import AgentState

APPLY_CONFIDENCE_THRESHOLD = 0.50
SKIP_CONFIDENCE_THRESHOLD = 0.80

DECISION_CRITERIA = {
    "apply": (
        "The candidate meets most important stated requirements, "
        "and the role aligns with the candidate's target career."
    ),
    "review": (
        "Important requirements are unclear, information is missing, "
        "or the position needs human judgment."
    ),
    "skip": (
        "The posting explicitly requires qualifications the candidate "
        "clearly lacks, or the role is explicitly unrelated."
    ),
}


def _route_decision(raw_choice: str, confidence: float) -> str:
    """Convert Jev's result into a conservative application decision."""

    if raw_choice == "skip":
        return "skip" if confidence >= SKIP_CONFIDENCE_THRESHOLD else "review"

    if raw_choice == "apply":
        return "apply" if confidence >= APPLY_CONFIDENCE_THRESHOLD else "review"

    return "review"


def jev_gate(state: AgentState) -> dict:
    """Filter only jobs that Jev identifies as clear, high-confidence mismatches."""

    settings = get_settings()
    profile = state.get("profile")
    jobs = state.get("jobs", [])

    if profile is None or not jobs:
        return {"jev_decisions": []}

    if not settings.jev_enabled or not settings.has_typesafe:
        return {"jev_decisions": []}

    jev_state = {
        "candidate": profile.model_dump(),
        "jobs": [
            {
                "job_id": job.job_id,
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "remote": job.remote,
                "description": job.description[:2000],
                "tags": job.tags,
            }
            for job in jobs
        ],
    }

    questions = {
        f"job_{index}": Choice(
            instructions=(
                f"Evaluate `jobs[{index}]` against `candidate`. "
                "Choose the candidate's next action. Missing information "
                "should lead to review, not skip. Choose skip only for an "
                "explicit, substantial mismatch."
            ),
            criteria=DECISION_CRITERIA,
        )
        for index in range(len(jobs))
    }

    try:
        with TypeSafeClient(
            api_key=settings.typesafe_api_key.get_secret_value()
        ) as client:
            response = client.system_one(
                state=jev_state,
                questions=questions,
            )

        decisions: list[JevJobDecision] = []
        kept_jobs = []

        for index, job in enumerate(jobs):
            answer = response.choices[f"job_{index}"]
            final_decision = _route_decision(
                answer.choice,
                answer.confidence,
            )

            decisions.append(
                JevJobDecision(
                    job_id=job.job_id,
                    raw_choice=answer.choice,
                    final_decision=final_decision,
                    confidence=answer.confidence,
                    probabilities=dict(answer.probabilities),
                )
            )

            # Remove only high-confidence explicit mismatches.
            if final_decision != "skip":
                kept_jobs.append(job)

    except Exception as error:
        errors = list(state.get("errors") or [])
        errors.append(f"Jev gate unavailable: {type(error).__name__}")
        return {
            "jev_decisions": [],
            "errors": errors,
        }

    return {
        "jobs": kept_jobs,
        "jev_decisions": decisions,
    }
