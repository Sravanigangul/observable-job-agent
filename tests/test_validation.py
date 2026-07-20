"""Fabrication validator: grounding checks, thresholds, degradation modes."""

from __future__ import annotations

from job_scout.corpus import build_corpus
from job_scout.graph.schemas import CVContent, ExperienceEntry, TailoredBullet, TailoringPack
from job_scout.validation import (
    BULLET_MATCH_THRESHOLD,
    LETTER_MATCH_THRESHOLD,
    SKILL_MATCH_THRESHOLD,
    validate_pack,
)
from tests.test_corpus import SAMPLE_CV


def _corpus():
    return build_corpus(SAMPLE_CV)


def _pack(
    bullets: list[TailoredBullet], skills: list[str] | None = None, letter: str = "Dear team,\nBest, Jane"
) -> TailoringPack:
    return TailoringPack(
        cv=CVContent(
            headline="Data Engineer",
            summary="Builds pipelines.",
            experience=[ExperienceEntry(role="Data Engineer", company="PipeCorp", dates="2022-2026", bullets=bullets)],
            skills=skills if skills is not None else ["Python"],
        ),
        cover_letter=letter,
    )


def test_thresholds_are_documented_constants():
    # Changing a threshold should be a conscious diff, not a drive-by tweak.
    assert BULLET_MATCH_THRESHOLD == 0.75
    assert SKILL_MATCH_THRESHOLD == 0.9
    assert LETTER_MATCH_THRESHOLD == 0.55


def test_close_rewrite_passes():
    # cv-bullet-002: "Built streaming ingestion handling 2M events/day."
    bullet = TailoredBullet(text="Built streaming ingestion pipelines handling 2M events/day", corpus_ref="cv-bullet-002")
    report = validate_pack(_pack([bullet]), _corpus())
    assert report.flags == 0


def test_fabricated_bullet_is_flagged():
    bullet = TailoredBullet(text="Led a 30-person org and shipped a self-driving car", corpus_ref="cv-bullet-002")
    report = validate_pack(_pack([bullet]), _corpus())
    assert report.flags == 1
    assert report.flagged[0].where == "cv_bullet:cv-bullet-002"
    assert report.flagged[0].best_match_ratio < BULLET_MATCH_THRESHOLD


def test_unresolvable_corpus_ref_is_flagged():
    bullet = TailoredBullet(text="Built streaming ingestion handling 2M events/day.", corpus_ref="cv-bullet-999")
    report = validate_pack(_pack([bullet]), _corpus())
    assert report.flags == 1
    assert "does not resolve" in report.flagged[0].reason


def test_skill_outside_corpus_is_flagged():
    report = validate_pack(_pack([], skills=["Python", "Kubernetes"]), _corpus())
    assert report.flags == 1
    assert report.flagged[0].where == "skill:Kubernetes"


def test_skill_normalization_tolerates_case():
    report = validate_pack(_pack([], skills=["python", "SQL"]), _corpus())
    assert report.flags == 0


def test_cover_letter_invented_metric_is_flagged():
    letter = "Dear team,\nI increased revenue by 300% at Google Cloud last year.\nBest regards, Jane"
    report = validate_pack(_pack([], letter=letter), _corpus())
    assert report.flags == 1
    assert report.flagged[0].where.startswith("cover_letter:sentence:")


def test_cover_letter_grounded_in_corpus_passes():
    letter = "Dear team,\nI built streaming ingestion handling 2M events/day at PipeCorp.\nBest regards, Jane"
    report = validate_pack(_pack([], letter=letter), _corpus())
    assert report.flags == 0


def test_cover_letter_grounded_in_research_passes():
    letter = "Dear team,\nI admire that Acme Rockets launched 12 missions in 2025.\nBest regards, Jane"
    research = "Acme Rockets launched 12 missions in 2025 and builds reusable boosters."
    flagged_without = validate_pack(_pack([], letter=letter), _corpus())
    passed_with = validate_pack(_pack([], letter=letter), _corpus(), research_notes=research)
    assert flagged_without.flags == 1
    assert passed_with.flags == 0


def test_cover_letter_job_context_not_flagged():
    letter = "Dear team,\nI am excited to apply for the Data Engineer role at Initech Systems in Berlin.\nBest regards, Jane"
    job_context = ["Data Engineer at Initech Systems", "Initech Systems"]
    report = validate_pack(_pack([], letter=letter), _corpus(), job_context=job_context)
    assert report.flags == 0


def test_non_factual_sentences_are_skipped():
    letter = "Dear team,\nI am deeply passionate about building wonderful things together with kind people.\nBest regards, Jane"
    report = validate_pack(_pack([], letter=letter), _corpus())
    assert report.flags == 0


def test_greeting_and_signoff_are_never_checked():
    letter = "Dear Initech Systems hiring team of 500 engineers!\nSincerely, Jane Doe, 42nd applicant"
    report = validate_pack(_pack([], letter=letter), _corpus())
    assert report.flags == 0
