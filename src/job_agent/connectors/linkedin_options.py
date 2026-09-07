from __future__ import annotations

import re
from dataclasses import dataclass, field

from job_agent.models import SearchQuery


TIME_POSTED_ALIASES = {
    "day": "r86400",
    "24h": "r86400",
    "week": "r604800",
    "month": "r2592000",
}

WORKPLACE_TYPE_ALIASES = {
    "onsite": "1",
    "on-site": "1",
    "remote": "2",
    "hybrid": "3",
}

JOB_TYPE_ALIASES = {
    "full_time": "F",
    "full-time": "F",
    "part_time": "P",
    "part-time": "P",
    "contract": "C",
    "temporary": "T",
    "internship": "I",
    "volunteer": "V",
    "other": "O",
}

EXPERIENCE_LEVEL_ALIASES = {
    "internship": "1",
    "entry": "2",
    "entry_level": "2",
    "associate": "3",
    "mid_senior": "4",
    "mid-senior": "4",
    "director": "5",
    "executive": "6",
}

SORT_BY_ALIASES = {"relevance": "R", "recent": "DD", "date": "DD"}
DURATION_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_duration_to_seconds(value: str) -> int | None:
    match = re.fullmatch(r"(\d+)\s*([mhdw])", value.strip().lower())
    if not match:
        return None
    amount, unit = match.groups()
    return int(amount) * DURATION_UNIT_SECONDS[unit]


def normalize_time_posted(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    alias = TIME_POSTED_ALIASES.get(value.lower())
    if alias:
        return alias
    seconds = parse_duration_to_seconds(value)
    return f"r{seconds}" if seconds is not None else value


def normalize_codes(values: list[str] | None, aliases: dict[str, str]) -> str | None:
    if not values:
        return None
    codes = [aliases.get(value.strip().lower(), value.strip()) for value in values if value.strip()]
    return ",".join(code for code in codes if code) or None


@dataclass(frozen=True, slots=True)
class LinkedInSearchOptions:
    """LinkedIn-specific parameter mapping, isolated from HTTP and HTML parsing."""

    geo_id: str | None = None
    time_posted: str | None = None
    experience_levels: str | None = None
    job_types: str | None = None
    workplace_types: str | None = None
    sort_by: str | None = None
    distance: int | None = None
    extra_params: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_inputs(
        cls,
        *,
        geo_id: str | None = None,
        time_posted: str | None = None,
        experience_levels: list[str] | None = None,
        job_types: list[str] | None = None,
        workplace_types: list[str] | None = None,
        sort_by: str | None = None,
        distance: int | None = None,
        extra_params: dict[str, str] | None = None,
    ) -> "LinkedInSearchOptions":
        if distance is not None and distance < 0:
            raise ValueError("LinkedIn distance cannot be negative")
        normalized_sort = SORT_BY_ALIASES.get((sort_by or "").strip().lower(), sort_by) or None
        return cls(
            geo_id=geo_id,
            time_posted=normalize_time_posted(time_posted),
            experience_levels=normalize_codes(experience_levels, EXPERIENCE_LEVEL_ALIASES),
            job_types=normalize_codes(job_types, JOB_TYPE_ALIASES),
            workplace_types=normalize_codes(workplace_types, WORKPLACE_TYPE_ALIASES),
            sort_by=normalized_sort,
            distance=distance,
            extra_params=dict(extra_params or {}),
        )

    def apply(self, params: dict[str, str], query: SearchQuery) -> dict[str, str]:
        result = dict(params)
        result.update(self.extra_params)
        configured = {
            "geoId": self.geo_id,
            "f_TPR": self.time_posted,
            "f_E": self.experience_levels,
            "f_JT": self.job_types,
            "f_WT": self.workplace_types,
            "sortBy": self.sort_by,
            "distance": str(self.distance) if self.distance is not None else None,
        }
        result.update({key: value for key, value in configured.items() if value is not None})

        result["keywords"] = query.keyword_text
        if query.location:
            result["location"] = query.location
        if query.posted_within_hours is not None:
            result["f_TPR"] = f"r{max(query.posted_within_hours, 0) * 3600}"
        elif query.posted_within_days is not None:
            result["f_TPR"] = f"r{max(query.posted_within_days, 0) * 86400}"
        if query.remote is True:
            result["f_WT"] = "2"
        elif query.remote is False:
            result["f_WT"] = "1,3"
        return result
