from __future__ import annotations

import httpx

from job_agent.models import NormalizedJob, SearchPage, SearchQuery
from job_agent.utils import fallback_job_id, infer_remote, parse_datetime, strip_html

from .base import BaseConnector


class JoobleConnector(BaseConnector):
    source = "jooble"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://uk.jooble.org",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Jooble API key is required")
        super().__init__(client)
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    async def search(self, query: SearchQuery, cursor: str | None = None) -> SearchPage:
        page = int(cursor or 1)
        body: dict[str, object] = {
            "keywords": query.keyword_text,
            "location": query.location or "",
            "page": page,
            "ResultOnPage": query.page_size,
        }
        if query.minimum_salary is not None:
            body["salary"] = int(query.minimum_salary)

        payload = await self._json_request(
            "POST",
            f"{self.base_url}/api/{self.api_key}",
            json=body,
            headers={"Content-Type": "application/json"},
        )
        rows = payload.get("jobs", [])
        total = int(payload.get("totalCount", len(rows)))
        jobs = [self._normalize(row) for row in rows]
        next_cursor = str(page + 1) if rows and page * query.page_size < total else None
        return SearchPage(jobs=jobs, next_cursor=next_cursor, total_available=total)

    def _normalize(self, row: dict) -> NormalizedJob:
        job_url = row.get("link") or row.get("url")
        job_id = str(row.get("id") or fallback_job_id(job_url, row.get("title"), row.get("company")))
        location = row.get("location")
        return NormalizedJob(
            source=self.source,
            source_job_id=job_id,
            title=row.get("title") or "Untitled job",
            company=row.get("company") or row.get("source") or "Unknown employer",
            location=location,
            description=strip_html(row.get("snippet") or row.get("description")),
            salary_text=row.get("salary"),
            employment_type=row.get("type"),
            remote=infer_remote(location),
            posted_at=parse_datetime(row.get("updated") or row.get("date")),
            job_url=job_url,
            apply_url=job_url,
            raw=row,
        )

