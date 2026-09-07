from __future__ import annotations

import httpx

from job_agent.models import NormalizedJob, SearchPage, SearchQuery
from job_agent.utils import as_float, infer_remote, job_matches_posted_age, parse_datetime

from .base import BaseConnector


class ReedConnector(BaseConnector):
    source = "reed"
    search_url = "https://www.reed.co.uk/api/1.0/search"

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        if not api_key:
            raise ValueError("Reed API key is required")
        super().__init__(client)
        self.api_key = api_key

    async def search(self, query: SearchQuery, cursor: str | None = None) -> SearchPage:
        offset = int(cursor or 0)
        page_size = min(query.page_size, 100)
        params: dict[str, object] = {
            "keywords": query.keyword_text,
            "resultsToTake": page_size,
            "resultsToSkip": offset,
        }
        if query.location:
            params["locationName"] = query.location
        if query.minimum_salary is not None:
            params["minimumSalary"] = query.minimum_salary
        if query.maximum_salary is not None:
            params["maximumSalary"] = query.maximum_salary

        payload = await self._json_request(
            "GET",
            self.search_url,
            params=params,
            auth=httpx.BasicAuth(self.api_key, ""),
        )
        rows = payload.get("results", [])
        total = int(payload.get("totalResults", len(rows)))
        jobs = [self._normalize(row) for row in rows]
        jobs = [job for job in jobs if job_matches_posted_age(job, query)]
        next_offset = offset + len(rows)
        next_cursor = str(next_offset) if rows and next_offset < total else None
        return SearchPage(jobs=jobs, next_cursor=next_cursor, total_available=total)

    def _normalize(self, row: dict) -> NormalizedJob:
        job_id = str(row.get("jobId") or row.get("id"))
        job_url = row.get("jobUrl") or f"https://www.reed.co.uk/jobs/{job_id}"
        location = row.get("locationName")
        return NormalizedJob(
            source=self.source,
            source_job_id=job_id,
            title=row.get("jobTitle") or "Untitled job",
            company=row.get("employerName") or "Unknown employer",
            location=location,
            description=row.get("jobDescription") or row.get("description"),
            salary_min=as_float(row.get("minimumSalary")),
            salary_max=as_float(row.get("maximumSalary")),
            salary_currency=row.get("currency") or "GBP",
            employment_type=row.get("contractType") or row.get("jobType"),
            remote=infer_remote(location),
            posted_at=parse_datetime(row.get("date") or row.get("postedDate")),
            expires_at=parse_datetime(row.get("expirationDate")),
            job_url=job_url,
            apply_url=row.get("externalUrl") or job_url,
            raw=row,
        )
