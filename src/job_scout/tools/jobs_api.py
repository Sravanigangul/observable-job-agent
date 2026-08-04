"""Job search exposed to the agent as a single ``search_jobs`` tool.

Behind the tool, ``run_search`` fans out to pluggable ``JobSource`` adapters and
merges their results:

    JSearch → Adzuna → Remotive → committed cache

The CONSUMPTION policy is a cascade: a source's results are only merged in when
the higher-priority sources returned too few jobs, so a reader with no API keys
still gets results from the offline cache. Since Phase 3 the live sources are
QUERIED concurrently (``SCOUT_CONCURRENT_SOURCES``, default on): the sequential
fallback used to stack network waits exactly when results were thinnest (the
"failing forward" 3s documented in docs/optimizing_latency.md). Consumption
order and thresholds are unchanged — only the waiting overlaps. No scraping
sources are included (see ``docs/extending_sources.md``).
"""

from __future__ import annotations

import contextvars
import json
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Protocol

import httpx
from langchain_core.tools import tool

from job_scout.config import get_settings
from job_scout.graph.schemas import JobPosting

DESCRIPTION_LIMIT = 4000
DEFAULT_LIMIT = 25
DEFAULT_COUNTRY = "us"
CACHE_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "cached_jobs.json"

_COUNTRY_CODES: dict[str, str] = {
    "united states": "us", "usa": "us", "us": "us", "america": "us",
    "united kingdom": "gb", "uk": "gb", "england": "gb", "london": "gb",
    "germany": "de", "deutschland": "de", "berlin": "de", "munich": "de", "münchen": "de",
    "india": "in", "bengaluru": "in", "bangalore": "in", "mumbai": "in", "delhi": "in",
    "australia": "au", "sydney": "au", "melbourne": "au",
    "brazil": "br", "brasil": "br", "são paulo": "br", "sao paulo": "br",
    "canada": "ca", "france": "fr", "spain": "es", "netherlands": "nl",
    "singapore": "sg", "poland": "pl", "italy": "it",
}  # fmt: skip


def location_to_country(location: str | None) -> str:
    """Map a free-text location to a two-letter country code (default ``us``)."""
    if not location:
        return DEFAULT_COUNTRY
    loc = location.strip().lower()
    for keyword, code in _COUNTRY_CODES.items():
        if keyword in loc:
            return code
    return DEFAULT_COUNTRY


def _truncate(text: str) -> str:
    """Cap a description at ``DESCRIPTION_LIMIT`` characters."""
    return (text or "")[:DESCRIPTION_LIMIT]


class JobSource(Protocol):
    """A pluggable jobs backend.

    Adapters must never raise on a network or parse error; they return an empty
    list so ``run_search`` can fall through to the next source.
    """

    name: str

    def fetch(self, query: str, location: str | None, country: str | None, remote: bool, limit: int) -> list[JobPosting]:
        """Return postings matching the query, or an empty list on any failure."""
        ...


class JSearchSource:
    """Official Google-for-Jobs aggregator (OpenWeb Ninja) with city-level search.

    Location is honoured deterministically: the location is folded into the query
    (``"<query> in <location>"``) and the country code is derived from it, so a
    Berlin CV returns Berlin jobs regardless of how the query was phrased.
    """

    name = "jsearch"
    BASE = "https://api.openwebninja.com/jsearch/search-v2"

    def __init__(self, api_key: str = "", timeout: float = 15.0) -> None:
        self.api_key = api_key or get_settings().jsearch_api_key.get_secret_value()
        self.timeout = timeout

    @property
    def available(self) -> bool:
        """Whether an API key is configured."""
        return bool(self.api_key)

    def fetch(self, query: str, location: str | None, country: str | None, remote: bool, limit: int) -> list[JobPosting]:
        """Fetch one page (10 results = 1 request credit; the free tier is small)."""
        if not self.available:
            return []
        params: dict[str, object] = {
            "query": f"{query} in {location}" if location else query,
            "country": country or location_to_country(location),
            "num_pages": 1,
        }
        if remote:
            params["work_from_home"] = "true"
        try:
            resp = httpx.get(self.BASE, params=params, headers={"X-API-Key": self.api_key}, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError, ValueError):
            return []
        payload = data.get("data")
        rows = payload.get("jobs") if isinstance(payload, dict) else payload if isinstance(payload, list) else []
        return [self._to_posting(r) for r in (rows or [])[:limit]]

    @staticmethod
    def _clean_location(r: dict) -> str:
        """Extract the location from JSearch, dropping the ``• via <publisher>`` suffix."""
        raw = (r.get("job_location") or "").split("•")[0].strip()
        if raw:
            return raw
        parts = [r.get("job_city"), r.get("job_state"), r.get("job_country")]
        return ", ".join(p for p in parts if p) or "Unspecified"

    @staticmethod
    def _to_posting(r: dict) -> JobPosting:
        """Convert one JSearch result into a ``JobPosting``."""
        return JobPosting(
            job_id=f"jsearch-{r.get('job_id') or r.get('id', '')}",
            title=(r.get("job_title") or "").strip() or "Untitled",
            company=(r.get("employer_name") or "").strip() or "Unknown",
            location=JSearchSource._clean_location(r),
            remote=bool(r.get("job_is_remote")),
            description=_truncate(r.get("job_description") or ""),
            url=r.get("job_apply_link") or "",
            tags=[t for t in [r.get("job_employment_type"), r.get("job_publisher")] if t],
            source="jsearch",
        )


class AdzunaSource:
    """Free official jobs API covering ~20 countries; needs an app id and key."""

    name = "adzuna"
    BASE = "https://api.adzuna.com/v1/api/jobs"

    def __init__(self, app_id: str = "", app_key: str = "", timeout: float = 10.0) -> None:
        settings = get_settings()
        self.app_id = app_id or settings.adzuna_app_id.get_secret_value()
        self.app_key = app_key or settings.adzuna_app_key.get_secret_value()
        self.timeout = timeout

    @property
    def available(self) -> bool:
        """Whether both credentials are configured."""
        return bool(self.app_id and self.app_key)

    def fetch(self, query: str, location: str | None, country: str | None, remote: bool, limit: int) -> list[JobPosting]:
        """Fetch postings for one country (derived from ``country`` or the location)."""
        if not self.available:
            return []
        code = country or location_to_country(location)
        params = {
            "app_id": self.app_id,
            "app_key": self.app_key,
            "results_per_page": min(limit, 50),
            "what": query,
            "content-type": "application/json",
        }
        if location:
            params["where"] = location
        try:
            resp = httpx.get(f"{self.BASE}/{code}/search/1", params=params, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError, ValueError):
            return []
        return [self._to_posting(r, code) for r in data.get("results", [])]

    @staticmethod
    def _to_posting(r: dict, code: str) -> JobPosting:
        """Convert one Adzuna result into a ``JobPosting``."""
        loc = (r.get("location") or {}).get("display_name") or code.upper()
        return JobPosting(
            job_id=f"adzuna-{r.get('id', '')}",
            title=r.get("title", "").strip() or "Untitled",
            company=(r.get("company") or {}).get("display_name", "").strip() or "Unknown",
            location=loc,
            remote="remote" in (r.get("title", "") + loc).lower(),
            description=_truncate(r.get("description", "")),
            url=r.get("redirect_url", ""),
            tags=[c.get("label", "") for c in [r.get("category", {})] if c.get("label")],
            source="adzuna",
        )


class RemotiveSource:
    """Keyless API of worldwide remote jobs."""

    name = "remotive"
    BASE = "https://remotive.com/api/remote-jobs"

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout

    def fetch(self, query: str, location: str | None, country: str | None, remote: bool, limit: int) -> list[JobPosting]:
        """Fetch remote postings matching the query."""
        try:
            resp = httpx.get(self.BASE, params={"search": query, "limit": limit}, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError, ValueError):
            return []
        return [self._to_posting(r) for r in data.get("jobs", [])[:limit]]

    @staticmethod
    def _to_posting(r: dict) -> JobPosting:
        """Convert one Remotive result into a ``JobPosting``."""
        return JobPosting(
            job_id=f"remotive-{r.get('id', '')}",
            title=r.get("title", "").strip() or "Untitled",
            company=r.get("company_name", "").strip() or "Unknown",
            location=r.get("candidate_required_location") or "Remote",
            remote=True,
            description=_truncate(r.get("description", "")),
            url=r.get("url", ""),
            tags=r.get("tags", []) or [],
            source="remotive",
        )


class CacheSource:
    """Offline fallback: keyword search over the committed ``cached_jobs.json``."""

    name = "cache"

    def __init__(self, path: Path = CACHE_PATH) -> None:
        self.path = path

    def _load(self) -> list[dict]:
        """Load the cached postings, or an empty list if the file is missing/invalid."""
        if not self.path.exists():
            return []
        try:
            return json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            return []

    def fetch(self, query: str, location: str | None, country: str | None, remote: bool, limit: int) -> list[JobPosting]:
        """Rank cached postings by how many query terms they contain."""
        terms = [t for t in re.split(r"\W+", query.lower()) if t]
        scored: list[tuple[int, dict]] = []
        for row in self._load():
            haystack = f"{row.get('title', '')} {row.get('description', '')} {' '.join(row.get('tags', []))}".lower()
            score = sum(1 for t in terms if t in haystack) + (1 if remote and row.get("remote") else 0)
            if score > 0 or not terms:
                scored.append((score, row))
        scored.sort(key=lambda s: s[0], reverse=True)
        return [
            JobPosting(**{**row, "source": "cache", "description": _truncate(row.get("description", ""))})
            for _, row in scored[:limit]
        ]


def _dedupe(jobs: list[JobPosting]) -> list[JobPosting]:
    """Drop jobs sharing a ``(title, company)`` with an earlier one."""
    seen: set[tuple[str, str]] = set()
    out: list[JobPosting] = []
    for job in jobs:
        key = (job.title.strip().lower(), job.company.strip().lower())
        if key not in seen:
            seen.add(key)
            out.append(job)
    return out


def run_search(
    query: str,
    location: str | None = None,
    country: str | None = None,
    remote: bool = False,
    limit: int = DEFAULT_LIMIT,
    *,
    jsearch: JSearchSource | None = None,
    adzuna: AdzunaSource | None = None,
    remotive: RemotiveSource | None = None,
    cache: CacheSource | None = None,
) -> tuple[list[JobPosting], list[str]]:
    """Search across the sources in order and return ``(jobs, sources_used)``.

    A source is only queried if the previous ones returned too few jobs. Results
    are merged, deduped by ``(title, company)`` and capped at ``limit``. The
    sources are injectable for testing. ``sources_used`` goes into trace metadata.
    """
    jsearch = jsearch or JSearchSource()
    adzuna = adzuna or AdzunaSource()
    remotive = remotive or RemotiveSource()
    cache = cache or CacheSource()

    jobs: list[JobPosting] = []
    used: list[str] = []

    # One span per source, so the trace answers "which source was slow" instead
    # of only "the search took 3 seconds" — the difference between diagnosing
    # the waterfall and guessing at it. See docs/ollie.md.
    #
    # Imported here, not at module scope: job_scout.tracing pulls in the graph,
    # which imports this module back (the pre-existing cold-import cycle).
    from job_scout.tracing import traced_call

    def _spanned(name: str, fn: Callable[[], list[JobPosting]]) -> Callable[[], list[JobPosting]]:
        return traced_call(f"source.{name}", fn, metadata={"source": name, "query": query, "location": location or ""})

    fetchers: dict[str, Callable[[], list[JobPosting]]] = {}
    if jsearch.available:
        fetchers["jsearch"] = _spanned("jsearch", lambda: jsearch.fetch(query, location, country, remote, limit))
    fetchers["adzuna"] = _spanned("adzuna", lambda: adzuna.fetch(query, location, country, remote, limit))
    fetchers["remotive"] = _spanned("remotive", lambda: remotive.fetch(query, location, country, remote, limit))

    concurrent = get_settings().scout_concurrent_sources and len(fetchers) > 1
    pool: ThreadPoolExecutor | None = None
    if concurrent:
        # Fire every live source at once; the cascade below decides what gets
        # consumed. copy_context keeps Opik tracer/cost contextvars intact in
        # worker threads (same pattern as rank_jobs).
        pool = ThreadPoolExecutor(max_workers=len(fetchers))
        futures = {name: pool.submit(contextvars.copy_context().run, fn) for name, fn in fetchers.items()}

        def fetch(name: str) -> list[JobPosting]:
            try:
                return futures[name].result() if name in futures else []
            except Exception:  # noqa: BLE001 - a dead source is an empty source
                return []
    else:

        def fetch(name: str) -> list[JobPosting]:
            try:
                return fetchers[name]() if name in fetchers else []
            except Exception:  # noqa: BLE001 - a dead source is an empty source
                return []

    def add(source_name: str, found: list[JobPosting]) -> None:
        """Record a source's results if it returned any."""
        if found:
            used.append(source_name)
            jobs.extend(found)

    try:
        add("jsearch", fetch("jsearch"))
        if len(_dedupe(jobs)) < 5:
            add("adzuna", fetch("adzuna"))
        if remote or len(_dedupe(jobs)) < 5:
            add("remotive", fetch("remotive"))
        if len(_dedupe(jobs)) < 3:
            add("cache", cache.fetch(query, location, country, remote, limit))
    finally:
        if pool is not None:
            pool.shutdown(wait=False)

    return _dedupe(jobs)[:limit], used


@tool
def search_jobs(query: str, country: str | None = None, remote: bool = False, limit: int = DEFAULT_LIMIT) -> list[dict]:
    """Search for open job postings matching a query.

    Args:
        query: Role/skill search terms, e.g. "machine learning engineer python".
        country: Two-letter country code (us, gb, de, in, au, br, ...). Omit to
            infer it from the query text.
        remote: Set true to prioritise remote-friendly roles.
        limit: Maximum number of postings to return.

    Returns:
        A list of job postings as dicts (title, company, location, description, url).
    """
    jobs, _sources = run_search(query=query, country=country, remote=remote, limit=limit)
    return [job.model_dump() for job in jobs]
