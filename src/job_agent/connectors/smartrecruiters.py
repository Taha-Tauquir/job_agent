from __future__ import annotations

import asyncio
from urllib.parse import quote

import httpx

from job_agent.models import NormalizedJob, SearchPage, SearchQuery
from job_agent.utils import infer_remote, job_matches_query, parse_datetime, strip_html

from .base import BaseConnector


class SmartRecruitersConnector(BaseConnector):
    def __init__(
        self,
        company_identifier: str,
        company_name: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not company_identifier:
            raise ValueError("SmartRecruiters company identifier is required")
        super().__init__(client)
        self.company_identifier = company_identifier
        self.company_name = company_name
        self.source = f"smartrecruiters:{company_identifier}"

    async def search(self, query: SearchQuery, cursor: str | None = None) -> SearchPage:
        offset = int(cursor or 0)
        company = quote(self.company_identifier, safe="")
        params: dict[str, object] = {"limit": query.page_size, "offset": offset}
        if query.keyword_text:
            params["q"] = query.keyword_text
        if query.location:
            params["city"] = query.location

        payload = await self._json_request(
            "GET",
            f"https://api.smartrecruiters.com/v1/companies/{company}/postings",
            params=params,
        )
        rows = payload.get("content", [])
        detail_results = await asyncio.gather(
            *(self._fetch_detail(company, row) for row in rows),
            return_exceptions=True,
        )
        enriched_rows = [
            detail if isinstance(detail, dict) else row
            for row, detail in zip(rows, detail_results, strict=True)
        ]
        jobs = [self._normalize(row) for row in enriched_rows]
        jobs = [job for job in jobs if job_matches_query(job, query)]
        total = int(payload.get("totalFound", len(rows)))
        next_offset = offset + len(rows)
        next_cursor = str(next_offset) if rows and next_offset < total else None
        return SearchPage(jobs=jobs, next_cursor=next_cursor, total_available=total)

    async def _fetch_detail(self, company: str, row: dict) -> dict:
        job_id = quote(str(row.get("id")), safe="")
        return await self._json_request(
            "GET",
            f"https://api.smartrecruiters.com/v1/companies/{company}/postings/{job_id}",
        )

    def _normalize(self, row: dict) -> NormalizedJob:
        company = row.get("company") or {}
        location_data = row.get("location") or {}
        employment = row.get("typeOfEmployment") or {}
        sections = ((row.get("jobAd") or {}).get("sections") or {})
        description_parts = [
            strip_html(section.get("text"))
            for section in sections.values()
            if isinstance(section, dict) and section.get("text")
        ]
        location = location_data.get("fullLocation") or ", ".join(
            filter(None, [location_data.get("city"), location_data.get("region"), location_data.get("country")])
        )
        job_id = str(row.get("id"))
        url = row.get("postingUrl") or row.get("ref")
        if url and url.startswith("https://api.smartrecruiters.com"):
            url = f"https://jobs.smartrecruiters.com/{self.company_identifier}/{job_id}"
        return NormalizedJob(
            source=self.source,
            source_job_id=job_id,
            title=row.get("name") or "Untitled job",
            company=self.company_name or company.get("name") or "Unknown employer",
            location=location or None,
            description=" ".join(filter(None, description_parts)) or None,
            employment_type=employment.get("label") if isinstance(employment, dict) else str(employment),
            remote=infer_remote(location, location_data.get("remote")),
            posted_at=parse_datetime(row.get("releasedDate")),
            job_url=row.get("postingUrl") or url,
            apply_url=row.get("applyUrl") or row.get("postingUrl") or url,
            raw=row,
        )
