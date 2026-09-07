from __future__ import annotations

import httpx

from job_agent.models import NormalizedJob, SearchPage, SearchQuery
from job_agent.utils import as_float, infer_remote, parse_datetime, strip_html

from .base import BaseConnector


class AdzunaConnector(BaseConnector):
    source = "adzuna"

    def __init__(
        self,
        app_id: str,
        app_key: str,
        country: str = "gb",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not app_id or not app_key:
            raise ValueError("Adzuna app_id and app_key are required")
        super().__init__(client)
        self.app_id = app_id
        self.app_key = app_key
        self.country = country.lower()

    async def search(self, query: SearchQuery, cursor: str | None = None) -> SearchPage:
        page = int(cursor or 1)
        country = query.country or self.country
        url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"
        params: dict[str, object] = {
            "app_id": self.app_id,
            "app_key": self.app_key,
            "results_per_page": query.page_size,
            "what": query.keyword_text,
        }
        if query.location:
            params["where"] = query.location
        if query.minimum_salary is not None:
            params["salary_min"] = query.minimum_salary
        if query.maximum_salary is not None:
            params["salary_max"] = query.maximum_salary
        if query.posted_within_days is not None:
            params["max_days_old"] = query.posted_within_days

        payload = await self._json_request("GET", url, params=params)
        rows = payload.get("results", [])
        total = int(payload.get("count", len(rows)))
        jobs = [self._normalize(row) for row in rows]
        next_cursor = str(page + 1) if rows and page * query.page_size < total else None
        return SearchPage(jobs=jobs, next_cursor=next_cursor, total_available=total)

    def _normalize(self, row: dict) -> NormalizedJob:
        company = row.get("company") or {}
        location_data = row.get("location") or {}
        category = row.get("category") or {}
        location = location_data.get("display_name")
        return NormalizedJob(
            source=self.source,
            source_job_id=str(row.get("id")),
            title=row.get("title") or "Untitled job",
            company=company.get("display_name") or "Unknown employer",
            location=location,
            description=strip_html(row.get("description")),
            salary_min=as_float(row.get("salary_min")),
            salary_max=as_float(row.get("salary_max")),
            salary_currency=row.get("salary_currency") or ("GBP" if self.country == "gb" else None),
            employment_type=row.get("contract_time") or row.get("contract_type"),
            remote=infer_remote(location),
            posted_at=parse_datetime(row.get("created")),
            job_url=row.get("redirect_url"),
            apply_url=row.get("redirect_url"),
            raw={**row, "normalized_category": category.get("label")},
        )

