<#
.SYNOPSIS
    Applies the "configurable LinkedIn filters" update by writing the updated
    files directly to their correct relative paths under the project root.

.DESCRIPTION
    No diffing/patching involved - each file's full final content is embedded
    below and written as-is, creating any needed subfolders. Existing files at
    those paths are overwritten (a .bak backup of each is kept alongside it).

.PARAMETER ProjectRoot
    Root of your job-agent project. Defaults to the current directory.

.EXAMPLE
    cd C:\Users\Hp\Desktop\ProjectCV\job-agent
    .\Apply-LinkedInFilters.ps1
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
    # Normalize forward slashes to this OS's separator so Join-Path/paths behave.
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
    # UTF8 without BOM
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($fullPath, $Content, $utf8NoBom)
    Write-Host "  wrote $normalizedRel" -ForegroundColor Green
}

Write-Host "Applying LinkedIn configurable-filters update to: $ProjectRoot"
Write-Host ""
$content__env_example = @'
# API-key sources
REED_API_KEY=
ADZUNA_APP_ID=
ADZUNA_APP_KEY=
ADZUNA_COUNTRY=gb
JOOBLE_API_KEY=
JOOBLE_BASE_URL=https://uk.jooble.org

# Comma-separated public company boards. Use board_id|Display Name when wanted.
GREENHOUSE_BOARDS=
LEVER_SITES=
ASHBY_BOARDS=
SMARTRECRUITERS_COMPANIES=

# Experimental: parses public HTML batches returned by this search URL.
# It does not bypass sign-in walls, challenges, rate limits, or result caps.
# Only "keywords" and "start" need to live in this URL - the filters below
# are layered on top of it, so you don't need to hand-edit the querystring
# every time you want to change a filter.
LINKEDIN_PUBLIC_SEARCH_URL=

# Optional LinkedIn filters (all optional - leave blank to skip a filter).
# geoId: LinkedIn's numeric location entity id (found in a LinkedIn search URL
#   once you've picked a location in the UI, e.g. 101165590 = United Kingdom).
LINKEDIN_GEO_ID=
# time_posted: day | week | month, or a raw code like r604800.
LINKEDIN_TIME_POSTED=
# experience_levels: comma list of internship, entry, associate, mid_senior,
#   director, executive (or raw numeric codes).
LINKEDIN_EXPERIENCE_LEVELS=
# job_types: comma list of full_time, part_time, contract, temporary,
#   internship, volunteer, other (or raw single-letter codes).
LINKEDIN_JOB_TYPES=
# workplace_types: comma list of onsite, remote, hybrid (or raw numeric codes).
LINKEDIN_WORKPLACE_TYPES=
# sort_by: relevance | recent (or a raw code like R / DD).
LINKEDIN_SORT_BY=
# distance: search radius in miles around the geoId location.
LINKEDIN_DISTANCE=
# extra_params: raw querystring fragment for anything not modeled above,
#   e.g. a company filter: f_C=1035&f_C=1441
LINKEDIN_EXTRA_PARAMS=
'@
Write-ProjectFile -RelativePath ".env.example" -Content $content__env_example

$content_src_job_agent_connectors_linkedin_public_py = @'
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from lxml import html

from job_agent.errors import ConnectorError
from job_agent.models import NormalizedJob, SearchPage, SearchQuery
from job_agent.utils import fallback_job_id, infer_remote, parse_datetime

from .base import BaseConnector

# Friendly aliases so `.env` can use words instead of LinkedIn's internal filter
# codes. Raw codes (e.g. "r604800", "2") are always accepted unchanged too.
_TIME_POSTED_ALIASES = {
    "day": "r86400",
    "24h": "r86400",
    "week": "r604800",
    "month": "r2592000",
}

_WORKPLACE_TYPE_ALIASES = {
    "onsite": "1",
    "on-site": "1",
    "remote": "2",
    "hybrid": "3",
}

_JOB_TYPE_ALIASES = {
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

_EXPERIENCE_LEVEL_ALIASES = {
    "internship": "1",
    "entry": "2",
    "entry_level": "2",
    "associate": "3",
    "mid_senior": "4",
    "mid-senior": "4",
    "director": "5",
    "executive": "6",
}

_SORT_BY_ALIASES = {
    "relevance": "R",
    "recent": "DD",
    "date": "DD",
}


def _normalize_time_posted(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    return _TIME_POSTED_ALIASES.get(value.lower(), value)


def _days_to_time_posted(days: int) -> str:
    return f"r{max(days, 0) * 86400}"


def _normalize_codes(values: list[str] | None, aliases: dict[str, str]) -> str | None:
    if not values:
        return None
    codes = [aliases.get(value.strip().lower(), value.strip()) for value in values if value.strip()]
    codes = [code for code in codes if code]
    return ",".join(codes) if codes else None


class LinkedInPublicConnector(BaseConnector):
    """Parse public HTML result batches from a configured LinkedIn search URL.

    This connector intentionally does not use private pagination endpoints,
    authenticated sessions, or challenge-bypass behavior.

    Filters are layered on top of the configured `search_url`, lowest to
    highest priority:
      1. Query parameters already present in `search_url` (e.g. a company
         filter someone pasted in manually).
      2. Filters passed to this constructor - normally sourced from
         environment variables by `registry.py`. These are the connector's
         standing configuration.
      3. Values on the `SearchQuery` for a single request (`location`,
         `remote`, `posted_within_days`), which override the static
         configuration for that request only.
    """

    source = "linkedin-public"

    def __init__(
        self,
        search_url: str,
        client: httpx.AsyncClient | None = None,
        *,
        geo_id: str | None = None,
        time_posted: str | None = None,
        experience_levels: list[str] | None = None,
        job_types: list[str] | None = None,
        workplace_types: list[str] | None = None,
        sort_by: str | None = None,
        distance: int | None = None,
        extra_params: dict[str, str] | None = None,
    ) -> None:
        valid_prefixes = (
            "https://www.linkedin.com/jobs/search/",
            "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
        )
        if not search_url.startswith(valid_prefixes):
            raise ValueError("A supported public linkedin.com jobs search URL is required")
        super().__init__(client)
        self.search_url = search_url
        self.geo_id = geo_id
        self.time_posted = _normalize_time_posted(time_posted)
        self.experience_levels = _normalize_codes(experience_levels, _EXPERIENCE_LEVEL_ALIASES)
        self.job_types = _normalize_codes(job_types, _JOB_TYPE_ALIASES)
        self.workplace_types = _normalize_codes(workplace_types, _WORKPLACE_TYPE_ALIASES)
        self.sort_by = _SORT_BY_ALIASES.get((sort_by or "").strip().lower(), sort_by) or None
        self.distance = distance
        self.extra_params = dict(extra_params or {})

    async def search(self, query: SearchQuery, cursor: str | None = None) -> SearchPage:
        is_fragment = "/jobs-guest/jobs/api/seeMoreJobPostings/search" in self.search_url
        url = self._url_for_query(query, cursor)
        try:
            response = await self.client.get(url)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ConnectorError(
                f"{self.source} returned HTTP {exc.response.status_code}; automated access stopped"
            ) from exc
        except httpx.HTTPError as exc:
            raise ConnectorError(f"{self.source} request failed: {exc}") from exc

        if not response.text.strip():
            if is_fragment:
                return SearchPage(jobs=[])
            raise ConnectorError("LinkedIn returned an empty search page")

        document = html.fromstring(response.text)
        cards = document.xpath('//li[.//div[contains(@class, "base-card")]]')
        if not cards:
            page_text = " ".join(document.text_content().split()).casefold()
            if "sign in" in page_text or "join now" in page_text:
                raise ConnectorError(
                    "LinkedIn returned a sign-in wall without public job cards"
                )
            if is_fragment and not page_text:
                return SearchPage(jobs=[])
            raise ConnectorError("LinkedIn returned no recognizable public job cards")

        jobs = [self._normalize(card) for card in cards]
        if is_fragment:
            start = int(dict(parse_qsl(urlsplit(url).query)).get("start", "0"))
            return SearchPage(jobs=jobs, next_cursor=str(start + len(cards)))
        return SearchPage(jobs=jobs, total_available=len(jobs))

    def _url_for_query(self, query: SearchQuery, cursor: str | None) -> str:
        parts = urlsplit(self.search_url)
        params = dict(parse_qsl(parts.query, keep_blank_values=True))
        params.pop("currentJobId", None)

        # 2. Statically configured filters (env-backed) sit on top of the base URL.
        params.update(self.extra_params)
        if self.geo_id:
            params["geoId"] = self.geo_id
        if self.time_posted:
            params["f_TPR"] = self.time_posted
        if self.experience_levels:
            params["f_E"] = self.experience_levels
        if self.job_types:
            params["f_JT"] = self.job_types
        if self.workplace_types:
            params["f_WT"] = self.workplace_types
        if self.sort_by:
            params["sortBy"] = self.sort_by
        if self.distance is not None:
            params["distance"] = str(self.distance)

        # 3. Per-request overrides from the search query win over static config.
        params["keywords"] = query.keyword_text
        if query.location:
            params["location"] = query.location
        if query.posted_within_days is not None:
            params["f_TPR"] = _days_to_time_posted(query.posted_within_days)
        if query.remote is True:
            params["f_WT"] = "2"
        elif query.remote is False:
            params["f_WT"] = "1,3"

        if "/jobs-guest/jobs/api/seeMoreJobPostings/search" in parts.path:
            params["start"] = cursor or params.get("start", "0")
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), ""))

    def _normalize(self, card) -> NormalizedJob:
        title = self._text(card, './/h3[contains(@class, "base-search-card__title")]')
        company = self._text(card, './/h4[contains(@class, "base-search-card__subtitle")]')
        location = self._text(card, './/span[contains(@class, "job-search-card__location")]')
        posted = self._attribute(card, ".//time", "datetime")
        raw_url = self._attribute(
            card,
            './/a[contains(@class, "base-card__full-link")]',
            "href",
        )
        entity = self._attribute(card, './/div[contains(@class, "base-card")]', "data-entity-urn")
        match = re.search(r"jobPosting:(\d+)", entity or "")
        job_id = match.group(1) if match else fallback_job_id(raw_url, title, company)
        job_url = self._canonical_url(raw_url)
        return NormalizedJob(
            source=self.source,
            source_job_id=job_id,
            title=title or "Untitled job",
            company=company or "Unknown employer",
            location=location,
            remote=infer_remote(location),
            posted_at=parse_datetime(posted),
            job_url=job_url,
            apply_url=job_url,
            raw={
                "entity_urn": entity,
                "search_result_url": raw_url,
                "configured_search_url": self.search_url,
            },
        )

    @staticmethod
    def _text(card, xpath: str) -> str | None:
        nodes = card.xpath(xpath)
        return " ".join(nodes[0].text_content().split()) if nodes else None

    @staticmethod
    def _attribute(card, xpath: str, name: str) -> str | None:
        nodes = card.xpath(xpath)
        return nodes[0].get(name) if nodes else None

    @staticmethod
    def _canonical_url(value: str | None) -> str:
        if not value:
            raise ConnectorError("LinkedIn job card did not include a link")
        parts = urlsplit(value)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
'@
Write-ProjectFile -RelativePath "src/job_agent/connectors/linkedin_public.py" -Content $content_src_job_agent_connectors_linkedin_public_py

$content_src_job_agent_registry_py = @'
from __future__ import annotations

import os
from collections.abc import Iterable
from urllib.parse import parse_qsl

from dotenv import load_dotenv

from .connectors import (
    AdzunaConnector,
    AshbyConnector,
    GreenhouseConnector,
    JoobleConnector,
    LeverConnector,
    LinkedInPublicConnector,
    ReedConnector,
    SmartRecruitersConnector,
)
from .connectors.base import BaseConnector


def _configured_boards(value: str | None) -> Iterable[tuple[str, str | None]]:
    for item in (value or "").split(","):
        item = item.strip()
        if not item:
            continue
        identifier, separator, display_name = item.partition("|")
        yield identifier.strip(), display_name.strip() if separator else None


def _csv_list(value: str | None) -> list[str] | None:
    if not value:
        return None
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or None


def _linkedin_extra_params(value: str | None) -> dict[str, str]:
    if not value:
        return {}
    return dict(parse_qsl(value.strip().lstrip("?")))


def build_connectors_from_env() -> dict[str, BaseConnector]:
    load_dotenv()
    connectors: list[BaseConnector] = []

    if reed_key := os.getenv("REED_API_KEY"):
        connectors.append(ReedConnector(reed_key))
    if adzuna_id := os.getenv("ADZUNA_APP_ID"):
        adzuna_key = os.getenv("ADZUNA_APP_KEY")
        if adzuna_key:
            connectors.append(
                AdzunaConnector(adzuna_id, adzuna_key, os.getenv("ADZUNA_COUNTRY", "gb"))
            )
    if jooble_key := os.getenv("JOOBLE_API_KEY"):
        connectors.append(
            JoobleConnector(jooble_key, os.getenv("JOOBLE_BASE_URL", "https://uk.jooble.org"))
        )

    for board, company in _configured_boards(os.getenv("GREENHOUSE_BOARDS")):
        connectors.append(GreenhouseConnector(board, company))
    for site, company in _configured_boards(os.getenv("LEVER_SITES")):
        connectors.append(LeverConnector(site, company))
    for board, company in _configured_boards(os.getenv("ASHBY_BOARDS")):
        connectors.append(AshbyConnector(board, company))
    for identifier, company in _configured_boards(os.getenv("SMARTRECRUITERS_COMPANIES")):
        connectors.append(SmartRecruitersConnector(identifier, company))

    if linkedin_url := os.getenv("LINKEDIN_PUBLIC_SEARCH_URL"):
        distance = os.getenv("LINKEDIN_DISTANCE")
        connectors.append(
            LinkedInPublicConnector(
                linkedin_url,
                geo_id=os.getenv("LINKEDIN_GEO_ID"),
                time_posted=os.getenv("LINKEDIN_TIME_POSTED"),
                experience_levels=_csv_list(os.getenv("LINKEDIN_EXPERIENCE_LEVELS")),
                job_types=_csv_list(os.getenv("LINKEDIN_JOB_TYPES")),
                workplace_types=_csv_list(os.getenv("LINKEDIN_WORKPLACE_TYPES")),
                sort_by=os.getenv("LINKEDIN_SORT_BY"),
                distance=int(distance) if distance else None,
                extra_params=_linkedin_extra_params(os.getenv("LINKEDIN_EXTRA_PARAMS")),
            )
        )

    return {connector.source: connector for connector in connectors}
'@
Write-ProjectFile -RelativePath "src/job_agent/registry.py" -Content $content_src_job_agent_registry_py

$content_tests_test_linkedin_public_py = @'
import asyncio

import httpx

from job_agent.connectors import LinkedInPublicConnector
from job_agent.models import SearchQuery


HTML = """
<html><body>
  <ul class="jobs-search__results-list">
    <li>
      <div class="base-card job-search-card" data-entity-urn="urn:li:jobPosting:4444978733">
        <a class="base-card__full-link" href="https://uk.linkedin.com/jobs/view/java-engineer-4444978733?trackingId=test"></a>
        <h3 class="base-search-card__title"> Java Engineer </h3>
        <h4 class="base-search-card__subtitle"> Example Bank </h4>
        <span class="job-search-card__location"> London, United Kingdom </span>
        <time datetime="2026-09-06">1 hour ago</time>
      </div>
    </li>
  </ul>
</body></html>
"""


def test_linkedin_public_html_is_normalized_without_tracking_parameters() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["keywords"] == "python"
        assert "currentJobId" not in request.url.params
        assert request.url.params["f_TPR"] == "r86400"
        return httpx.Response(200, text=HTML)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = LinkedInPublicConnector(
                "https://www.linkedin.com/jobs/search/?currentJobId=1&f_TPR=r86400&keywords=java",
                client,
            )
            return await connector.search(SearchQuery(keywords=["python"]))

    page = asyncio.run(scenario())
    assert page.total_available == 1
    assert page.next_cursor is None
    assert page.jobs[0].source_job_id == "4444978733"
    assert page.jobs[0].title == "Java Engineer"
    assert page.jobs[0].company == "Example Bank"
    assert page.jobs[0].job_url == "https://uk.linkedin.com/jobs/view/java-engineer-4444978733"


def test_linkedin_guest_fragment_advances_by_returned_card_count() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["start"] == "100"
        return httpx.Response(200, text=HTML)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = LinkedInPublicConnector(
                "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=java&start=0",
                client,
            )
            return await connector.search(SearchQuery(keywords=["java"]), cursor="100")

    page = asyncio.run(scenario())
    assert len(page.jobs) == 1
    assert page.next_cursor == "101"
    assert page.total_available is None


def test_linkedin_public_stops_on_sign_in_wall_without_cards() -> None:
    async def scenario():
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text="<html><body>Sign in to continue</body></html>")
        )
        async with httpx.AsyncClient(transport=transport) as client:
            connector = LinkedInPublicConnector(
                "https://www.linkedin.com/jobs/search/?keywords=java",
                client,
            )
            return await connector.search(SearchQuery(keywords=["java"]))

    try:
        asyncio.run(scenario())
    except RuntimeError as exc:
        assert "sign-in wall" in str(exc)
    else:
        raise AssertionError("Expected the connector to reject a sign-in wall")



def test_linkedin_configured_filters_are_applied_to_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        assert params["geoId"] == "101165590"
        assert params["f_TPR"] == "r604800"  # "week" alias
        assert params["f_E"] == "3,4"  # associate, mid_senior
        assert params["f_JT"] == "F"  # full_time
        assert params["f_WT"] == "2"  # remote
        assert params["sortBy"] == "DD"  # recent
        assert params["distance"] == "25"
        assert params["f_C"] == "1035"  # extra_params passthrough
        return httpx.Response(200, text=HTML)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = LinkedInPublicConnector(
                "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=java&start=0",
                client,
                geo_id="101165590",
                time_posted="week",
                experience_levels=["associate", "mid_senior"],
                job_types=["full_time"],
                workplace_types=["remote"],
                sort_by="recent",
                distance=25,
                extra_params={"f_C": "1035"},
            )
            return await connector.search(SearchQuery(keywords=["java"]))

    page = asyncio.run(scenario())
    assert len(page.jobs) == 1


def test_linkedin_query_overrides_configured_time_posted_and_remote() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        # Static config says "month" and doesn't set remote; the per-request
        # query asks for the last day and remote-only, which must win.
        assert params["f_TPR"] == "r86400"
        assert params["f_WT"] == "2"
        return httpx.Response(200, text=HTML)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = LinkedInPublicConnector(
                "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=java&start=0",
                client,
                time_posted="month",
            )
            query = SearchQuery(keywords=["java"], posted_within_days=1, remote=True)
            return await connector.search(query)

    page = asyncio.run(scenario())
    assert len(page.jobs) == 1


def test_linkedin_query_location_is_applied() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["location"] == "Manchester"
        return httpx.Response(200, text=HTML)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = LinkedInPublicConnector(
                "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=java&start=0",
                client,
            )
            return await connector.search(SearchQuery(keywords=["java"], location="Manchester"))

    page = asyncio.run(scenario())
    assert len(page.jobs) == 1
'@
Write-ProjectFile -RelativePath "tests/test_linkedin_public.py" -Content $content_tests_test_linkedin_public_py

Write-Host ""
Write-Host "Done. Review the changes, then run:" -ForegroundColor Cyan
Write-Host "  python -m pytest tests/test_linkedin_public.py -q"
