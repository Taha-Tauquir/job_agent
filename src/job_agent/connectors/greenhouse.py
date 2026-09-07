from __future__ import annotations

from urllib.parse import quote

import httpx

from job_agent.models import NormalizedJob, SearchPage, SearchQuery
from job_agent.utils import infer_remote, job_matches_query, parse_datetime, strip_html

from .base import BaseConnector


class GreenhouseConnector(BaseConnector):
    def __init__(
        self,
        board_token: str,
        company_name: str | None = None,
        include_content: bool = True,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not board_token:
            raise ValueError("Greenhouse board token is required")
        super().__init__(client)
        self.board_token = board_token
        self.company_name = company_name or board_token.replace("-", " ").title()
        self.include_content = include_content
        self.source = f"greenhouse:{board_token}"

    async def search(self, query: SearchQuery, cursor: str | None = None) -> SearchPage:
        del cursor
        token = quote(self.board_token, safe="")
        payload = await self._json_request(
            "GET",
            f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
            params={"content": str(self.include_content).lower()},
        )
        jobs = [self._normalize(row) for row in payload.get("jobs", [])]
        jobs = [job for job in jobs if job_matches_query(job, query)]
        return SearchPage(jobs=jobs, total_available=int(payload.get("meta", {}).get("total", len(jobs))))

    def _normalize(self, row: dict) -> NormalizedJob:
        location = (row.get("location") or {}).get("name")
        url = row.get("absolute_url")
        return NormalizedJob(
            source=self.source,
            source_job_id=str(row.get("id")),
            title=row.get("title") or "Untitled job",
            company=self.company_name,
            location=location,
            description=strip_html(row.get("content")),
            remote=infer_remote(location),
            posted_at=parse_datetime(row.get("updated_at")),
            job_url=url,
            apply_url=url,
            raw=row,
        )

