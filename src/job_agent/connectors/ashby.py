from __future__ import annotations

from urllib.parse import quote

import httpx

from job_agent.models import NormalizedJob, SearchPage, SearchQuery
from job_agent.utils import as_float, infer_remote, job_matches_query, parse_datetime

from .base import BaseConnector


class AshbyConnector(BaseConnector):
    def __init__(
        self,
        job_board_name: str,
        company_name: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not job_board_name:
            raise ValueError("Ashby job board name is required")
        super().__init__(client)
        self.job_board_name = job_board_name
        self.company_name = company_name or job_board_name.replace("-", " ").title()
        self.source = f"ashby:{job_board_name}"

    async def search(self, query: SearchQuery, cursor: str | None = None) -> SearchPage:
        del cursor
        board = quote(self.job_board_name, safe="")
        payload = await self._json_request(
            "GET",
            f"https://api.ashbyhq.com/posting-api/job-board/{board}",
            params={"includeCompensation": "true"},
        )
        jobs = [self._normalize(row) for row in payload.get("jobs", []) if row.get("isListed", True)]
        jobs = [job for job in jobs if job_matches_query(job, query)]
        return SearchPage(jobs=jobs, total_available=len(payload.get("jobs", [])))

    def _normalize(self, row: dict) -> NormalizedJob:
        compensation = row.get("compensation") or {}
        location = row.get("location")
        return NormalizedJob(
            source=self.source,
            source_job_id=str(row.get("id")),
            title=row.get("title") or "Untitled job",
            company=self.company_name,
            location=location,
            description=row.get("descriptionPlain"),
            salary_min=as_float(compensation.get("minValue") or compensation.get("min")),
            salary_max=as_float(compensation.get("maxValue") or compensation.get("max")),
            salary_currency=compensation.get("currencyCode") or compensation.get("currency"),
            salary_interval=compensation.get("interval"),
            salary_text=compensation.get("summary"),
            employment_type=row.get("employmentType"),
            remote=infer_remote(location, row.get("isRemote")),
            posted_at=parse_datetime(row.get("publishedAt")),
            job_url=row.get("jobUrl"),
            apply_url=row.get("applyUrl") or row.get("jobUrl"),
            raw=row,
        )

