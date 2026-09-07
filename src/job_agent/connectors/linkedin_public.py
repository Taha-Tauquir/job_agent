from __future__ import annotations

import asyncio
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from job_agent.models import SearchPage, SearchQuery
from job_agent.linkedin_job_details import LinkedInJobFetcher
from job_agent.parsers import LinkedInSearchParser
from job_agent.transport import RequestPolicy

from .linkedin_options import LinkedInSearchOptions
from .request_response import RequestResponseConnector, RequestSpec


class LinkedInPublicConnector(RequestResponseConnector):
    """LinkedIn adapter: options -> request -> normalized response.

    The endpoint is public but undocumented. This connector deliberately does
    not use authenticated sessions or attempt to bypass access challenges.
    """

    source = "linkedin-public"
    _fragment_path = "/jobs-guest/jobs/api/seeMoreJobPostings/search"

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
        request_delay_seconds: float = 2.0,
        max_retries_on_rate_limit: int = 2,
        include_job_details: bool = True,
        max_detail_concurrency: int = 4,
        parser: LinkedInSearchParser | None = None,
    ) -> None:
        valid_prefixes = (
            "https://www.linkedin.com/jobs/search/",
            "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
        )
        if not search_url.startswith(valid_prefixes):
            raise ValueError("A supported public linkedin.com jobs search URL is required")
        if max_detail_concurrency < 1:
            raise ValueError("max_detail_concurrency must be at least 1")
        super().__init__(
            client,
            request_policy=RequestPolicy(
                minimum_interval_seconds=request_delay_seconds,
                rate_limit_retries=max_retries_on_rate_limit,
                backoff_base_seconds=request_delay_seconds,
            ),
        )
        self.search_url = search_url
        self.options = LinkedInSearchOptions.from_inputs(
            geo_id=geo_id,
            time_posted=time_posted,
            experience_levels=experience_levels,
            job_types=job_types,
            workplace_types=workplace_types,
            sort_by=sort_by,
            distance=distance,
            extra_params=extra_params,
        )
        self.parser = parser or LinkedInSearchParser()
        self.include_job_details = include_job_details
        self.max_detail_concurrency = max_detail_concurrency
        self.detail_fetcher = LinkedInJobFetcher(
            self.client,
            request_delay_seconds=request_delay_seconds,
            max_retries_on_rate_limit=max_retries_on_rate_limit,
        )

    async def close(self) -> None:
        await self.detail_fetcher.close()
        await super().close()

    async def search(self, query: SearchQuery, cursor: str | None = None) -> SearchPage:
        page = await super().search(query, cursor)
        if not self.include_job_details or not page.jobs:
            return page

        semaphore = asyncio.Semaphore(self.max_detail_concurrency)

        async def enrich(job):
            async with semaphore:
                try:
                    detail = await self.detail_fetcher.fetch(job.job_url)
                except Exception as exc:
                    provider_data = dict(job.provider_data)
                    provider_data["detail_error"] = str(exc)
                    return job.model_copy(update={"provider_data": provider_data})

                provider_data = dict(job.provider_data)
                provider_data["linkedin_detail"] = detail.model_dump(mode="json")
                return job.model_copy(
                    update={
                        "title": detail.title or job.title,
                        "company": detail.company or job.company,
                        "location": detail.location or job.location,
                        "description": detail.description or job.description,
                        "employment_type": detail.employment_type or job.employment_type,
                        "posted_at": detail.posted_at or job.posted_at,
                        "expires_at": detail.valid_through or job.expires_at,
                        "application_deadline": detail.valid_through or job.application_deadline,
                        "provider_data": provider_data,
                    }
                )

        jobs = await asyncio.gather(*(enrich(job) for job in page.jobs))
        return page.model_copy(update={"jobs": jobs})

    @property
    def is_fragment_source(self) -> bool:
        return self._fragment_path in urlsplit(self.search_url).path

    def build_request(self, query: SearchQuery, cursor: str | None) -> RequestSpec:
        return RequestSpec(method="GET", url=self._url_for_query(query, cursor))

    def parse_response(
        self,
        response: httpx.Response,
        query: SearchQuery,
        cursor: str | None,
    ) -> SearchPage:
        jobs = self.parser.parse(
            response.text,
            source=self.source,
            configured_search_url=self.search_url,
            allow_empty=self.is_fragment_source,
        )
        if not self.is_fragment_source:
            return SearchPage(jobs=jobs, total_available=len(jobs))
        if not jobs:
            return SearchPage(jobs=[])

        request_url = response.request.url
        start = int(dict(parse_qsl(request_url.query.decode())).get("start", "0"))
        return SearchPage(jobs=jobs, next_cursor=str(start + len(jobs)))

    def _url_for_query(self, query: SearchQuery, cursor: str | None) -> str:
        parts = urlsplit(self.search_url)
        params = dict(parse_qsl(parts.query, keep_blank_values=True))
        params.pop("currentJobId", None)
        params = self.options.apply(params, query)
        if self.is_fragment_source:
            params["start"] = cursor or params.get("start", "0")
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), ""))
