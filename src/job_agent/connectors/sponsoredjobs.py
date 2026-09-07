from __future__ import annotations

import asyncio
import logging
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from job_agent.models import NormalizedJob, SearchPage, SearchQuery
from job_agent.parsers.sponsoredjobs import SponsoredJobsParser
from job_agent.transport import RequestPolicy
from job_agent.utils import job_matches_query

from .base import BaseConnector


logger = logging.getLogger("uvicorn.error")


class SponsoredJobsConnector(BaseConnector):
    """Fetch Sponsored Jobs listings and enrich every card from its detail page."""

    source = "sponsoredjobs"

    def __init__(
        self,
        search_url: str = "https://sponsoredjobs.co.uk/jobs/sector/it?per_page=100",
        client: httpx.AsyncClient | None = None,
        *,
        request_delay_seconds: float = 0.25,
        max_retries_on_rate_limit: int = 2,
        max_detail_concurrency: int = 8,
        parser: SponsoredJobsParser | None = None,
    ) -> None:
        parts = urlsplit(search_url)
        if parts.scheme != "https" or parts.netloc != "sponsoredjobs.co.uk":
            raise ValueError("A sponsoredjobs.co.uk HTTPS search URL is required")
        if max_detail_concurrency < 1:
            raise ValueError("max_detail_concurrency must be at least 1")
        super().__init__(
            client,
            RequestPolicy(
                minimum_interval_seconds=request_delay_seconds,
                rate_limit_retries=max_retries_on_rate_limit,
                backoff_base_seconds=max(request_delay_seconds, 1.0),
            ),
        )
        self.search_url = search_url
        self.max_detail_concurrency = max_detail_concurrency
        self.parser = parser or SponsoredJobsParser()

    async def search(self, query: SearchQuery, cursor: str | None = None) -> SearchPage:
        page_url = self._page_url(cursor)
        response = await self._request("GET", page_url)
        listing_jobs, next_cursor, total = self.parser.parse_listing(
            response.text,
            page_url=page_url,
            source=self.source,
        )
        logger.info(
            "SPONSOREDJOBS_LISTING parsed_jobs=%s total_reported=%s next_cursor=%s",
            len(listing_jobs),
            total,
            next_cursor,
        )
        semaphore = asyncio.Semaphore(self.max_detail_concurrency)

        async def enrich(job: NormalizedJob) -> NormalizedJob:
            async with semaphore:
                try:
                    detail_response = await self._request("GET", job.job_url)
                    return self.parser.parse_detail(detail_response.text, job=job)
                except Exception as exc:
                    provider_data = dict(job.provider_data)
                    provider_data["detail_error"] = str(exc)
                    return job.model_copy(update={"provider_data": provider_data})

        enriched = await asyncio.gather(*(enrich(job) for job in listing_jobs))
        matching = [job for job in enriched if job_matches_query(job, query)]
        return SearchPage(jobs=matching, next_cursor=next_cursor, total_available=total)

    def _page_url(self, cursor: str | None) -> str:
        parts = urlsplit(self.search_url)
        params = dict(parse_qsl(parts.query, keep_blank_values=True))
        params.setdefault("per_page", "100")
        if cursor:
            params["page"] = cursor
        else:
            params.pop("page", None)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), ""))
