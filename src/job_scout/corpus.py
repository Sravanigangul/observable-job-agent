"""The candidate corpus: the ONLY permissible content source for tailoring.

Every bullet, skill, and claim in a tailored application must trace back to a
``CorpusItem`` here. The corpus is built from the CV text and, optionally, an
official LinkedIn data-export ZIP (Settings → "Get a copy of your data").

Segmentation is a deliberately simple line/keyword heuristic — no NLP
libraries. It will mis-classify the odd line on unusual CV layouts; that is an
accepted, documented limitation (findings material, not a bug to engineer
away).

Privacy: a user's LinkedIn export contains personal data. This module only ever
receives a filesystem *path*; the ZIP is never committed, logged, or attached
to a trace. The only committed exports are the synthetic fixtures under
``data/fixture_linkedin/`` (see ``scripts/generate_fixture_linkedin.py``).
"""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

CorpusKind = Literal["bullet", "skill", "education", "summary"]
CorpusSource = Literal["cv", "linkedin"]

# Section-heading keywords → the kind of items the section yields. Includes the
# German headings used by the non-English fixture CV. Anything unmatched is
# treated as experience (bullets) — the safest default for tailoring.
_SUMMARY_HEADINGS = {"summary", "profile", "about", "objective", "zusammenfassung", "profil"}
_SKILL_HEADINGS = {"skills", "kenntnisse", "technologies", "tools", "competencies", "skills & tools"}
_EDUCATION_HEADINGS = {"education", "ausbildung", "bildung", "studium"}

_BULLET_GLYPHS = ("-", "•", "*", "–", "◦")

# LinkedIn export files we understand. Official exports vary by account and
# region; every file is optional and anything missing is silently skipped.
_LI_POSITIONS = "Positions.csv"
_LI_SKILLS = "Skills.csv"
_LI_EDUCATION = "Education.csv"
_LI_PROFILE = "Profile.csv"


class CorpusItem(BaseModel):
    """One verifiable unit of the candidate's real experience."""

    id: str
    text: str
    kind: CorpusKind
    source: CorpusSource
    section: str  # the heading (CV) or file (LinkedIn) it came from


class CandidateCorpus(BaseModel):
    """All corpus items, addressable by id for grounding checks."""

    items: list[CorpusItem] = Field(default_factory=list)

    def get(self, item_id: str) -> CorpusItem | None:
        """Look up one item by its id, or ``None``."""
        return next((item for item in self.items if item.id == item_id), None)

    def skills(self) -> list[str]:
        """Texts of every skill item (the allowed skill vocabulary)."""
        return [item.text for item in self.items if item.kind == "skill"]

    def render_for_prompt(self) -> str:
        """Render as ``[id] text`` lines the tailor LLM selects from."""
        return "\n".join(f"[{item.id}] {item.text}" for item in self.items)


def build_corpus(cv_text: str, linkedin_zip: str | Path | None = None) -> CandidateCorpus:
    """Build the corpus from CV text plus an optional LinkedIn export ZIP."""
    items = _segment_cv(cv_text)
    if linkedin_zip:
        items.extend(_parse_linkedin_zip(Path(linkedin_zip)))
    return CandidateCorpus(items=items)


def _looks_like_heading(line: str) -> bool:
    """A short standalone word-or-two line reads as a section heading."""
    words = line.split()
    return 0 < len(words) <= 3 and not line.startswith(_BULLET_GLYPHS) and "," not in line and not line.rstrip().endswith(".")


def _section_kind(heading: str) -> CorpusKind:
    """Map a section heading to the kind of items it yields."""
    lowered = heading.lower().strip(" :")
    if lowered in _SUMMARY_HEADINGS:
        return "summary"
    if lowered in _SKILL_HEADINGS:
        return "skill"
    if lowered in _EDUCATION_HEADINGS:
        return "education"
    return "bullet"


def _segment_cv(cv_text: str) -> list[CorpusItem]:
    """Split raw CV text into corpus items using line/keyword heuristics."""
    items: list[CorpusItem] = []
    counters: dict[str, int] = {}
    section = ""
    kind: CorpusKind = "summary"  # leading paragraph before any heading

    def add(text: str, item_kind: CorpusKind, item_section: str) -> None:
        text = text.strip()
        if len(text) < 3:
            return
        counters[item_kind] = counters.get(item_kind, 0) + 1
        items.append(
            CorpusItem(
                id=f"cv-{item_kind}-{counters[item_kind]:03d}",
                text=text,
                kind=item_kind,
                source="cv",
                section=item_section or "(top)",
            )
        )

    for raw_line in cv_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Contact/header noise: emails, pipes-separated contact rows.
        if "@" in line or "|" in line:
            continue
        if _looks_like_heading(line):
            section = line
            kind = _section_kind(line)
            continue
        if kind == "skill":
            for skill in line.rstrip(".").split(","):
                add(skill, "skill", section)
        elif kind in ("summary", "education"):
            add(line, kind, section)
        else:
            add(line.lstrip("".join(_BULLET_GLYPHS)).strip(), "bullet", section)
    return items


def _read_csv(archive: zipfile.ZipFile, filename: str) -> list[dict[str, str]]:
    """Read one CSV from the export, tolerating absence and bad encodings."""
    member = next((n for n in archive.namelist() if Path(n).name == filename), None)
    if member is None:
        return []
    try:
        with archive.open(member) as fh:
            text = io.TextIOWrapper(fh, encoding="utf-8-sig", errors="replace")
            return list(csv.DictReader(text))
    except (csv.Error, KeyError, OSError):
        return []


def _parse_linkedin_zip(path: Path) -> list[CorpusItem]:
    """Parse an official LinkedIn data-export ZIP with stdlib only.

    Raises ``ValueError`` for a missing or non-ZIP file (a user-facing input
    error); missing/malformed CSVs inside a valid ZIP are skipped silently.
    """
    if not path.exists():
        raise ValueError(f"LinkedIn export not found: {path}")
    if not zipfile.is_zipfile(path):
        raise ValueError(f"Not a ZIP file: {path} — upload the official LinkedIn data export")

    items: list[CorpusItem] = []
    with zipfile.ZipFile(path) as archive:
        for row in _read_csv(archive, _LI_PROFILE):
            for field in ("Headline", "Summary"):
                value = (row.get(field) or "").strip()
                if value:
                    items.append(
                        CorpusItem(
                            id=f"li-summary-{len(items) + 1:03d}",
                            text=value,
                            kind="summary",
                            source="linkedin",
                            section=_LI_PROFILE,
                        )
                    )

        for i, row in enumerate(_read_csv(archive, _LI_POSITIONS), start=1):
            title = (row.get("Title") or "").strip()
            company = (row.get("Company Name") or "").strip()
            dates = " - ".join(filter(None, [(row.get("Started On") or "").strip(), (row.get("Finished On") or "").strip()]))
            if title or company:
                header = ", ".join(filter(None, [title, company])) + (f" ({dates})" if dates else "")
                items.append(
                    CorpusItem(id=f"li-pos-{i:03d}", text=header, kind="bullet", source="linkedin", section=_LI_POSITIONS)
                )
            description = (row.get("Description") or "").strip()
            for b, sentence in enumerate((s.strip() for s in description.splitlines() if s.strip()), start=1):
                items.append(
                    CorpusItem(
                        id=f"li-pos-{i:03d}-b{b}",
                        text=sentence.lstrip("".join(_BULLET_GLYPHS)).strip(),
                        kind="bullet",
                        source="linkedin",
                        section=_LI_POSITIONS,
                    )
                )

        for i, row in enumerate(_read_csv(archive, _LI_SKILLS), start=1):
            name = (row.get("Name") or "").strip()
            if name:
                items.append(CorpusItem(id=f"li-skill-{i:03d}", text=name, kind="skill", source="linkedin", section=_LI_SKILLS))

        for i, row in enumerate(_read_csv(archive, _LI_EDUCATION), start=1):
            school = (row.get("School Name") or "").strip()
            degree = (row.get("Degree Name") or "").strip()
            text = ", ".join(filter(None, [degree, school]))
            if text:
                items.append(
                    CorpusItem(id=f"li-edu-{i:03d}", text=text, kind="education", source="linkedin", section=_LI_EDUCATION)
                )

    return items
