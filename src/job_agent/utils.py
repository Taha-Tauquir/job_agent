from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Iterable

from .models import NormalizedJob, SearchQuery


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


def strip_html(value: str | None) -> str | None:
    if not value:
        return None
    parser = _TextExtractor()
    parser.feed(value)
    text = " ".join(parser.parts)
    return re.sub(r"\s+", " ", text).strip() or None


def parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    if isinstance(value, str):
        candidate = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S"):
                try:
                    parsed = datetime.strptime(candidate, fmt)
                    break
                except ValueError:
                    continue
            else:
                return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fallback_job_id(*values: str | None) -> str:
    material = "|".join(value or "" for value in values)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def join_nonempty(values: Iterable[str | None], separator: str = ", ") -> str | None:
    cleaned = [str(value).strip() for value in values if value and str(value).strip()]
    return separator.join(cleaned) or None


def infer_remote(location: str | None, explicit: Any = None) -> bool | None:
    if isinstance(explicit, bool):
        return explicit
    if location and "remote" in location.casefold():
        return True
    return None


def contains_search_term(text: str, term: str) -> bool:
    """Match a complete search term so `java` does not match `javascript`."""
    normalized_term = term.strip().casefold()
    if not normalized_term:
        return True
    pattern = rf"(?<!\w){re.escape(normalized_term)}(?!\w)"
    return re.search(pattern, text.casefold()) is not None


def job_matches_query(job: NormalizedJob, query: SearchQuery) -> bool:
    haystack = " ".join(
        part for part in (job.title, job.company, job.description or "") if part
    ).casefold()
    if query.keywords and not all(contains_search_term(haystack, keyword) for keyword in query.keywords):
        return False

    if query.remote is True and job.remote is not True:
        return False
    if query.remote is False and job.remote is True:
        return False

    if query.location and not query.remote:
        location = (job.location or "").casefold()
        if query.location.casefold() not in location:
            return False

    if query.minimum_salary is not None and job.salary_max is not None:
        if job.salary_max < query.minimum_salary:
            return False
    if query.maximum_salary is not None and job.salary_min is not None:
        if job.salary_min > query.maximum_salary:
            return False

    if query.posted_within_hours is not None and job.posted_at is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=query.posted_within_hours)
        if job.posted_at.astimezone(timezone.utc) < cutoff:
            return False
    elif query.posted_within_days is not None and job.posted_at is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=query.posted_within_days)
        if job.posted_at.astimezone(timezone.utc) < cutoff:
            return False
    return True


def job_matches_posted_age(job: NormalizedJob, query: SearchQuery) -> bool:
    """Apply only the local posting-age part of a query.

    API-backed connectors should not repeat provider keyword/location matching:
    providers may use stemming, synonyms, and fields absent from their response.
    """
    if query.posted_within_hours is not None:
        if job.posted_at is None:
            return False
        cutoff = datetime.now(timezone.utc) - timedelta(hours=query.posted_within_hours)
        return job.posted_at.astimezone(timezone.utc) >= cutoff
    if query.posted_within_days is not None:
        if job.posted_at is None:
            return False
        cutoff = datetime.now(timezone.utc) - timedelta(days=query.posted_within_days)
        return job.posted_at.astimezone(timezone.utc) >= cutoff
    return True


_DURATION_UNIT_SECONDS = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
    "w": 604800,
}


def parse_duration_seconds(value: str) -> int | None:
    """Parse a short duration string like "30s", "90m", "6h", "3d", "2w" into
    seconds. Returns None if the string doesn't match that shape."""
    match = re.fullmatch(r"(\d+)\s*([smhdw])", value.strip().lower())
    if not match:
        return None
    amount, unit = match.groups()
    return int(amount) * _DURATION_UNIT_SECONDS[unit]
