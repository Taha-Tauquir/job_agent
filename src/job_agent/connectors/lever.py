from __future__ import annotations

from urllib.parse import quote

import httpx

from job_agent.models import NormalizedJob, SearchPage, SearchQuery
from job_agent.utils import as_float, infer_remote, job_matches_query, parse_datetime, strip_html

from .base import BaseConnector


class LeverConnector(BaseConnector):
    def __init__(
        self,
        site: str,
        company_name: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not site:
            raise ValueError("Lever site name is required")
        super().__init__(client)
        self.site = site
        self.company_name = company_name or site.replace("-", " ").title()
        self.source = f"lever:{site}"

    async def search(self, query: SearchQuery, cursor: str | None = None) -> SearchPage:
        skip = int(cursor or 0)
        site = quote(self.site, safe="")
        payload = await self._json_request(
            "GET",
            f"https://api.lever.co/v0/postings/{site}",
            params={"mode": "json", "skip": skip, "limit": query.page_size},
        )
        jobs = [self._normalize(row) for row in payload]
        jobs = [job for job in jobs if job_matches_query(job, query)]
        next_cursor = str(skip + len(payload)) if len(payload) == query.page_size else None
        return SearchPage(jobs=jobs, next_cursor=next_cursor)

    def _normalize(self, row: dict) -> NormalizedJob:
        categories = row.get("categories") or {}
        salary = row.get("salaryRange") or {}
        location = categories.get("location")
        description = row.get("descriptionPlain") or strip_html(row.get("description"))
        if not description:
            description = " ".join(
                filter(None, [row.get("openingPlain"), row.get("additionalPlain")])
            ) or None
        return NormalizedJob(
            source=self.source,
            source_job_id=str(row.get("id")),
            title=row.get("text") or "Untitled job",
            company=self.company_name,
            location=location,
            description=description,
            salary_min=as_float(salary.get("min")),
            salary_max=as_float(salary.get("max")),
            salary_currency=salary.get("currency"),
            salary_interval=salary.get("interval"),
            salary_text=row.get("salaryDescriptionPlain"),
            employment_type=categories.get("commitment"),
            remote=infer_remote(location, row.get("workplaceType") == "remote"),
            posted_at=parse_datetime(row.get("createdAt")),
            job_url=row.get("hostedUrl"),
            apply_url=row.get("applyUrl") or row.get("hostedUrl"),
            raw=row,
        )

