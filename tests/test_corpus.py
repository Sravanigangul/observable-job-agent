"""CandidateCorpus: CV segmentation, LinkedIn export parsing, provenance."""

from __future__ import annotations

from pathlib import Path

import pytest

from job_scout.corpus import CandidateCorpus, CorpusItem, build_corpus

FIXTURE_LINKEDIN = Path(__file__).resolve().parent.parent / "data" / "fixture_linkedin"

SAMPLE_CV = """\
Jane Doe
Berlin, Germany  |  jane.doe@example.com  |  English

Summary
Data engineer with 4 years of experience building pipelines.

Skills
Python, SQL, Airflow, dbt.

Experience
Data Engineer, PipeCorp (2022-2026)
- Built streaming ingestion handling 2M events/day.
- Migrated the warehouse from Redshift to BigQuery.

Education
B.Sc. Computer Science, HU Berlin (2021).
"""


def test_segmentation_kinds_and_sections():
    corpus = build_corpus(SAMPLE_CV)
    kinds = {item.kind for item in corpus.items}
    assert kinds == {"summary", "skill", "bullet", "education"}

    skills = corpus.skills()
    assert skills == ["Python", "SQL", "Airflow", "dbt"]

    bullets = [item for item in corpus.items if item.kind == "bullet"]
    assert any("streaming ingestion" in b.text for b in bullets)
    assert all(b.section == "Experience" for b in bullets)
    # Bullet glyphs are stripped from the stored text.
    assert not any(b.text.startswith("-") for b in bullets)


def test_segmentation_skips_contact_lines():
    corpus = build_corpus(SAMPLE_CV)
    assert not any("@" in item.text for item in corpus.items)
    assert not any(item.text == "Jane Doe" for item in corpus.items)


def test_ids_are_stable_and_unique():
    corpus = build_corpus(SAMPLE_CV)
    ids = [item.id for item in corpus.items]
    assert len(ids) == len(set(ids))
    assert corpus.get("cv-skill-001").text == "Python"
    assert corpus.get("nope") is None


def test_render_for_prompt_includes_ids():
    corpus = build_corpus(SAMPLE_CV)
    rendered = corpus.render_for_prompt()
    assert "[cv-skill-001] Python" in rendered


def test_build_from_cv_plus_linkedin_fixture():
    zip_path = FIXTURE_LINKEDIN / "senior_mle_eu_li_export.zip"
    corpus = build_corpus(SAMPLE_CV, zip_path)

    sources = {item.source for item in corpus.items}
    assert sources == {"cv", "linkedin"}

    li_items = [item for item in corpus.items if item.source == "linkedin"]
    assert any(item.kind == "skill" for item in li_items)
    assert any(item.kind == "bullet" and "DeepMobility" in item.text for item in li_items)
    assert any(item.kind == "education" for item in li_items)
    assert any(item.kind == "summary" for item in li_items)
    # LinkedIn items carry their source file as the section (provenance).
    assert all(item.section.endswith(".csv") for item in li_items)


def test_linkedin_missing_file_raises_value_error(tmp_path):
    with pytest.raises(ValueError, match="not found"):
        build_corpus(SAMPLE_CV, tmp_path / "missing.zip")


def test_linkedin_non_zip_raises_value_error(tmp_path):
    bogus = tmp_path / "export.zip"
    bogus.write_text("not a zip")
    with pytest.raises(ValueError, match="Not a ZIP"):
        build_corpus(SAMPLE_CV, bogus)


def test_linkedin_zip_without_known_csvs_yields_no_items(tmp_path):
    import zipfile

    path = tmp_path / "odd.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Unrelated.csv", "A,B\n1,2\n")
    corpus = build_corpus(SAMPLE_CV, path)
    assert all(item.source == "cv" for item in corpus.items)


def test_empty_cv_yields_empty_corpus():
    assert build_corpus("").items == []


def test_corpus_get_and_skills_on_empty():
    corpus = CandidateCorpus(items=[CorpusItem(id="x", text="Python", kind="skill", source="cv", section="Skills")])
    assert corpus.skills() == ["Python"]
