<#
.SYNOPSIS
    Applies the "LinkedIn job-description fetcher" update by writing the
    updated files directly to their correct relative paths under the project
    root - no diffing/patching involved.

.DESCRIPTION
    Adds a new /api/linkedin/job-descriptions endpoint that fetches one or
    more LinkedIn job-view pages and extracts title/company/location/
    description from the embedded JobPosting JSON-LD block.

    Writes 3 files:
      - src/job_agent/linkedin_job_details.py   (new)
      - src/job_agent/models.py                 (full replacement)
      - src/job_agent/api.py                     (full replacement)

    Existing files at those paths are backed up first as <name>.bak.

.PARAMETER ProjectRoot
    Root of your job-agent project. Defaults to the current directory.

.EXAMPLE
    cd C:\Users\Hp\Desktop\ProjectCV\job-agent
    .\Apply-LinkedInJobDescriptions.ps1
#>

[CmdletBinding()]
param(
    [string]$ProjectRoot = (Get-Location).Path
)

if (-not (Test-Path -LiteralPath $ProjectRoot)) {
    Write-Error "ProjectRoot '$ProjectRoot' does not exist."
    exit 1
}
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path

function Write-ProjectFile {
    param(
        [string]$RelativePath,
        [string]$Content
    )
    $normalizedRel = $RelativePath -replace '/', [System.IO.Path]::DirectorySeparatorChar
    $fullPath = Join-Path $ProjectRoot $normalizedRel
    $dir = Split-Path -Parent $fullPath
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    if (Test-Path -LiteralPath $fullPath) {
        Copy-Item -LiteralPath $fullPath -Destination "$fullPath.bak" -Force
        Write-Host "  backed up existing file to $normalizedRel.bak"
    }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($fullPath, $Content, $utf8NoBom)
    Write-Host "  wrote $normalizedRel" -ForegroundColor Green
}

Write-Host "Applying LinkedIn job-descriptions update to: $ProjectRoot"
Write-Host ""
$content_src_job_agent_models_py = @'
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
    posted_within_days: int | None = Field(default=None, ge=0, le=365)
    posted_within_hours: int | None = Field(default=None, ge=0, le=8760)
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
    remote: bool | None = None
    posted_at: datetime | None = None
    expires_at: datetime | None = None
    job_url: str
    apply_url: str | None = None
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
'@
Write-ProjectFile -RelativePath "src/job_agent/models.py" -Content $content_src_job_agent_models_py

$content_src_job_agent_api_py = @'
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from .linkedin_job_details import LinkedInJobFetcher
from .models import (
    LinkedInJobDescriptionRequest,
    LinkedInJobDescriptionResult,
    LinkedInJobDescriptionsResponse,
    Schedule,
    ScheduleCreateRequest,
    ScheduleUpdateRequest,
    SearchQuery,
    SearchRequest,
    SearchResponse,
)
from .registry import build_connectors_from_env
from .scheduler import JobScheduler, ScheduleNotFoundError
from .service import SearchService


@asynccontextmanager
async def lifespan(app: FastAPI):
    connectors = build_connectors_from_env()
    app.state.search_service = SearchService(connectors)
    app.state.scheduler = JobScheduler(app.state.search_service)
    yield
    await app.state.scheduler.close()
    await app.state.search_service.close()


# Environment-backed source configuration is loaded by the application lifespan.
app = FastAPI(title="Personal Job Agent API", version="0.2.0", lifespan=lifespan)


@app.get("/api/health")
async def health() -> dict[str, object]:
    return {"status": "ok", "configured_sources": list(app.state.search_service.connectors)}


@app.get("/api/sources")
async def sources() -> dict[str, list[str]]:
    return {"sources": list(app.state.search_service.connectors)}


@app.post("/api/search", response_model=SearchResponse)
async def search(request: SearchRequest) -> SearchResponse:
    query = SearchQuery(**request.model_dump(exclude={"sources", "max_pages_per_source"}))
    results = await app.state.search_service.search_all(
        query,
        request.sources,
        request.max_pages_per_source,
    )
    return SearchResponse(query=query, results=results)


@app.post("/api/schedules", response_model=Schedule, status_code=201)
async def create_schedule(request: ScheduleCreateRequest) -> Schedule:
    try:
        return app.state.scheduler.create(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/schedules", response_model=list[Schedule])
async def list_schedules() -> list[Schedule]:
    return app.state.scheduler.list_schedules()


@app.get("/api/schedules/{schedule_id}", response_model=Schedule)
async def get_schedule(schedule_id: str) -> Schedule:
    try:
        return app.state.scheduler.get(schedule_id)
    except ScheduleNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Schedule not found") from exc


@app.patch("/api/schedules/{schedule_id}", response_model=Schedule)
async def update_schedule(schedule_id: str, request: ScheduleUpdateRequest) -> Schedule:
    try:
        return app.state.scheduler.update(schedule_id, request)
    except ScheduleNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Schedule not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/api/schedules/{schedule_id}", status_code=204)
async def delete_schedule(schedule_id: str) -> None:
    try:
        app.state.scheduler.delete(schedule_id)
    except ScheduleNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Schedule not found") from exc


@app.post("/api/schedules/{schedule_id}/run-now", response_model=Schedule)
async def run_schedule_now(schedule_id: str) -> Schedule:
    try:
        return await app.state.scheduler.run_now(schedule_id)
    except ScheduleNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Schedule not found") from exc


@app.post("/api/linkedin/job-descriptions", response_model=LinkedInJobDescriptionsResponse)
async def fetch_linkedin_job_descriptions(
    request: LinkedInJobDescriptionRequest,
) -> LinkedInJobDescriptionsResponse:
    fetcher = LinkedInJobFetcher()
    results: list[LinkedInJobDescriptionResult] = []
    try:
        # Sequential on purpose: this shares one fetcher's self-throttle so
        # a batch of URLs doesn't hit LinkedIn back-to-back.
        for url in request.urls:
            try:
                job = await fetcher.fetch(url)
                results.append(LinkedInJobDescriptionResult(url=url, job=job))
            except Exception as exc:
                results.append(LinkedInJobDescriptionResult(url=url, error=str(exc)))
    finally:
        await fetcher.close()
    return LinkedInJobDescriptionsResponse(results=results)
'@
Write-ProjectFile -RelativePath "src/job_agent/api.py" -Content $content_src_job_agent_api_py

$content_src_job_agent_linkedin_job_details_py = @'
from __future__ import annotations

import asyncio
import html
import json
import re
import time
from urllib.parse import urlsplit

import httpx

from .errors import ConnectorError
from .models import LinkedInJobDetail
from .utils import parse_datetime, strip_html

_VALID_HOST_SUFFIX = "linkedin.com"
_JOB_VIEW_PATH_MARKER = "/jobs/view/"

# LinkedIn job-view URLs end in "-<numeric job id>", e.g.
# .../software-engineer-java-at-fanduel-4449566015
_JOB_ID_PATTERN = re.compile(r"-(\d+)/?$")

# Every /jobs/view/ page embeds a JobPosting JSON-LD block alongside a
# BreadcrumbList block; we want the one with "@type":"JobPosting".
_LD_JSON_PATTERN = re.compile(
    r'<script type="application/ld\+json">\s*(\{.*?\})\s*</script>', re.S
)


class LinkedInJobFetcher:
    """Fetches a single public LinkedIn job-posting page and extracts its
    details from the embedded JobPosting JSON-LD block - the same structured
    data LinkedIn exposes to search engines. This is far more stable to parse
    than the rendered HTML around it (which clamps/escapes content and is
    prone to markup changes).

    Requests are self-throttled and back off on 429s, same approach as
    LinkedInPublicConnector uses for search pagination.
    """

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        request_delay_seconds: float = 2.0,
        max_retries_on_rate_limit: int = 2,
    ) -> None:
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(30),
            follow_redirects=True,
            headers={"User-Agent": "PersonalJobAgent/0.1"},
        )
        self.request_delay_seconds = request_delay_seconds
        self.max_retries_on_rate_limit = max_retries_on_rate_limit
        self._last_request_at: float | None = None

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    @staticmethod
    def _validate_url(url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise ValueError(f"Not a valid URL: {url}")
        if not parts.netloc.endswith(_VALID_HOST_SUFFIX):
            raise ValueError(f"Not a linkedin.com URL: {url}")
        if _JOB_VIEW_PATH_MARKER not in parts.path:
            raise ValueError(f"Expected a '/jobs/view/...' job posting URL, got: {url}")

    async def fetch(self, job_url: str) -> LinkedInJobDetail:
        self._validate_url(job_url)
        response = await self._get_with_self_throttle(job_url)

        posting = self._extract_job_posting_json_ld(response.text)
        job_id = self._extract_job_id(job_url) or str(
            (posting.get("identifier") or {}).get("value") or ""
        ) or None

        hiring_org = posting.get("hiringOrganization") or {}
        job_location = posting.get("jobLocation") or {}
        address = job_location.get("address") or {}
        location_parts = [
            address.get("addressLocality"),
            address.get("addressRegion"),
            address.get("addressCountry"),
        ]
        location = ", ".join(part for part in location_parts if part) or None

        raw_description = posting.get("description")
        description = strip_html(html.unescape(raw_description)) if raw_description else None

        return LinkedInJobDetail(
            url=job_url,
            job_id=job_id,
            title=posting.get("title"),
            company=hiring_org.get("name"),
            location=location,
            employment_type=posting.get("employmentType"),
            description=description,
            posted_at=parse_datetime(posting.get("datePosted")),
            valid_through=parse_datetime(posting.get("validThrough")),
        )

    async def _get_with_self_throttle(self, url: str) -> httpx.Response:
        await self._wait_for_next_slot()
        attempt = 0
        while True:
            try:
                response = await self.client.get(url)
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429 and attempt < self.max_retries_on_rate_limit:
                    attempt += 1
                    retry_after = self._parse_retry_after(exc.response.headers.get("retry-after"))
                    wait_for = (
                        retry_after
                        if retry_after is not None
                        else self.request_delay_seconds * (2 ** attempt)
                    )
                    await asyncio.sleep(wait_for)
                    continue
                raise ConnectorError(
                    f"linkedin-job-details returned HTTP {exc.response.status_code}; "
                    "automated access stopped"
                ) from exc
            except httpx.HTTPError as exc:
                raise ConnectorError(f"linkedin-job-details request failed: {exc}") from exc

    async def _wait_for_next_slot(self) -> None:
        now = time.monotonic()
        if self._last_request_at is not None:
            remaining = self.request_delay_seconds - (now - self._last_request_at)
            if remaining > 0:
                await asyncio.sleep(remaining)
        self._last_request_at = time.monotonic()

    @staticmethod
    def _parse_retry_after(value: str | None) -> float | None:
        if not value:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    @staticmethod
    def _extract_job_id(job_url: str) -> str | None:
        match = _JOB_ID_PATTERN.search(urlsplit(job_url).path)
        return match.group(1) if match else None

    @staticmethod
    def _extract_job_posting_json_ld(page_html: str) -> dict:
        for match in _LD_JSON_PATTERN.finditer(page_html):
            try:
                data = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            if data.get("@type") == "JobPosting":
                return data
        raise ConnectorError(
            "Could not find a JobPosting JSON-LD block on the page; "
            "LinkedIn may have changed its page structure or shown a sign-in wall"
        )
'@
Write-ProjectFile -RelativePath "src/job_agent/linkedin_job_details.py" -Content $content_src_job_agent_linkedin_job_details_py

Write-Host ""
Write-Host "Done. Restart uvicorn, then test with:" -ForegroundColor Cyan
Write-Host '  curl.exe -X POST http://127.0.0.1:8000/api/linkedin/job-descriptions -H "Content-Type: application/json" -d "{\"urls\":[\"https://uk.linkedin.com/jobs/view/software-engineer-java-at-fanduel-4449566015\"]}"'