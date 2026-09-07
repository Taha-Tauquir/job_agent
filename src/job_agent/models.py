from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, computed_field, field_validator


class SearchQuery(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    location: str | None = None
    remote: bool | None = None
    minimum_salary: float | None = Field(default=None, ge=0)
    maximum_salary: float | None = Field(default=None, ge=0)
    posted_within_days: int | None = Field(
        default=None,
        ge=0,
        le=365,
        description="Keep jobs posted within this many days. Ignored when posted_within_hours is set.",
    )
    posted_within_hours: int | None = Field(
        default=None,
        ge=0,
        le=8760,
        description="Keep jobs posted within this many hours. Takes precedence over posted_within_days.",
    )
    country: str = Field(default="gb", min_length=2, max_length=2)
    page_size: int = Field(default=50, ge=1, le=100)

    @field_validator("keywords")
    @classmethod
    def clean_keywords(cls, value: list[str]) -> list[str]:
        return [keyword.strip() for keyword in value if keyword.strip()]

    @field_validator("country")
    @classmethod
    def normalize_country(cls, value: str) -> str:
        return value.lower()

    @computed_field
    @property
    def keyword_text(self) -> str:
        return " ".join(self.keywords)


class NormalizedJob(BaseModel):
    source: str
    source_job_id: str
    title: str
    company: str
    location: str | None = None
    description: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_interval: str | None = None
    salary_text: str | None = None
    employment_type: str | None = None
    workplace_type: str | None = None
    remote: bool | None = None
    posted_at: datetime | None = None
    expires_at: datetime | None = None
    application_deadline: datetime | None = None
    sponsorship_status: str | None = None
    visa_routes: list[str] = Field(default_factory=list)
    soc_code: str | None = None
    going_rate_status: str | None = None
    sector: str | None = None
    description_html: str | None = None
    job_url: str
    apply_url: str | None = None
    provider_data: dict[str, Any] = Field(default_factory=dict)
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)


class SearchPage(BaseModel):
    jobs: list[NormalizedJob]
    next_cursor: str | None = None
    total_available: int | None = None

    @property
    def has_more(self) -> bool:
        return self.next_cursor is not None


class SourceSearchResult(BaseModel):
    source: str
    jobs: list[NormalizedJob] = Field(default_factory=list)
    pages_fetched: int = 0
    total_reported: int | None = None
    error: str | None = None


class SearchRequest(SearchQuery):
    sources: list[str] | None = None
    max_pages_per_source: int | None = Field(default=20, ge=1, le=500)


class ProviderSearchRequest(SearchQuery):
    """Search request for an endpoint already scoped to one provider."""

    max_pages_per_source: int | None = Field(default=20, ge=1, le=500)


class SearchResponse(BaseModel):
    query: SearchQuery
    results: list[SourceSearchResult]

    @computed_field
    @property
    def jobs_found(self) -> int:
        return sum(len(result.jobs) for result in self.results)


def _duration_to_seconds(value: str) -> int | None:
    import re

    match = re.fullmatch(r"(\d+)\s*([smhdw])", value.strip().lower())
    if not match:
        return None
    amount, unit = match.groups()
    return int(amount) * {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]


class ScheduleCreateRequest(BaseModel):
    """Request body to create a recurring background search."""

    name: str | None = None
    query: SearchQuery = Field(default_factory=SearchQuery)
    sources: list[str] | None = None
    max_pages_per_source: int | None = Field(default=20, ge=1, le=500)
    interval: str = Field(
        description="How often to run, e.g. '1h', '30m', '15m', '2h', '1d'."
    )
    enabled: bool = True

    @field_validator("interval")
    @classmethod
    def validate_interval(cls, value: str) -> str:
        if _duration_to_seconds(value) is None:
            raise ValueError("interval must look like '30m', '1h', '2h', '1d', etc.")
        if _duration_to_seconds(value) < 60:
            raise ValueError("interval must be at least 1 minute")
        return value


class ScheduleUpdateRequest(BaseModel):
    """Partial update for an existing schedule."""

    enabled: bool | None = None
    interval: str | None = None

    @field_validator("interval")
    @classmethod
    def validate_interval(cls, value: str | None) -> str | None:
        if value is None:
            return None
        seconds = _duration_to_seconds(value)
        if seconds is None:
            raise ValueError("interval must look like '30m', '1h', '2h', '1d', etc.")
        if seconds < 60:
            raise ValueError("interval must be at least 1 minute")
        return value


class ScheduleRunSummary(BaseModel):
    started_at: datetime
    finished_at: datetime
    jobs_found: int
    errors: dict[str, str] = Field(default_factory=dict)
    jobs: list[NormalizedJob] = Field(default_factory=list)


class Schedule(BaseModel):
    id: str
    name: str | None = None
    query: SearchQuery
    sources: list[str] | None = None
    max_pages_per_source: int | None = None
    interval: str
    interval_seconds: int
    enabled: bool
    created_at: datetime
    next_run_at: datetime | None = None
    last_run: ScheduleRunSummary | None = None


class LinkedInJobDetail(BaseModel):
    url: str
    job_id: str | None = None
    title: str | None = None
    company: str | None = None
    location: str | None = None
    employment_type: str | None = None
    description: str | None = None
    posted_at: datetime | None = None
    valid_through: datetime | None = None


class LinkedInJobDescriptionRequest(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=25)


class LinkedInJobDescriptionResult(BaseModel):
    url: str
    job: LinkedInJobDetail | None = None
    error: str | None = None


class LinkedInJobDescriptionsResponse(BaseModel):
    results: list[LinkedInJobDescriptionResult]
