from __future__ import annotations

import html
import json
import re
from urllib.parse import urlsplit

import httpx

from .errors import ConnectorError
from .models import LinkedInJobDetail
from .transport import HttpTransport, RequestPolicy
from .utils import parse_datetime, strip_html

_VALID_HOST_SUFFIX = "linkedin.com"
_JOB_VIEW_PATH_MARKER = "/jobs/view/"

# LinkedIn job-view URLs end in "-<numeric job id>", e.g.
# .../software-engineer-java-at-fanduel-4449566015
_JOB_ID_PATTERN = re.compile(r"-(\d+)/?$")

# Every /jobs/view/ page embeds a JobPosting JSON-LD block alongside a
# BreadcrumbList block; we want the one with "@type":"JobPosting".
_LD_JSON_PATTERN = re.compile(
    r'<script type="application/ld\+json">\s*(\{.*?\})\s*</script>', re.S
)


class LinkedInJobFetcher:
    """Fetches a single public LinkedIn job-posting page and extracts its
    details from the embedded JobPosting JSON-LD block - the same structured
    data LinkedIn exposes to search engines. This is far more stable to parse
    than the rendered HTML around it (which clamps/escapes content and is
    prone to markup changes).

    Requests are self-throttled and back off on 429s, same approach as
    LinkedInPublicConnector uses for search pagination.
    """

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        request_delay_seconds: float = 2.0,
        max_retries_on_rate_limit: int = 2,
    ) -> None:
        self.transport = HttpTransport(
            client,
            RequestPolicy(
                minimum_interval_seconds=request_delay_seconds,
                rate_limit_retries=max_retries_on_rate_limit,
                backoff_base_seconds=request_delay_seconds,
            ),
        )
        self.client = self.transport.client

    async def close(self) -> None:
        await self.transport.close()

    @staticmethod
    def _validate_url(url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise ValueError(f"Not a valid URL: {url}")
        if not parts.netloc.endswith(_VALID_HOST_SUFFIX):
            raise ValueError(f"Not a linkedin.com URL: {url}")
        if _JOB_VIEW_PATH_MARKER not in parts.path:
            raise ValueError(f"Expected a '/jobs/view/...' job posting URL, got: {url}")

    async def fetch(self, job_url: str) -> LinkedInJobDetail:
        self._validate_url(job_url)
        response = await self.transport.request("linkedin-job-details", "GET", job_url)

        posting = self._extract_job_posting_json_ld(response.text)
        job_id = self._extract_job_id(job_url) or str(
            (posting.get("identifier") or {}).get("value") or ""
        ) or None

        hiring_org = posting.get("hiringOrganization") or {}
        job_location = posting.get("jobLocation") or {}
        address = job_location.get("address") or {}
        location_parts = [
            address.get("addressLocality"),
            address.get("addressRegion"),
            address.get("addressCountry"),
        ]
        location = ", ".join(part for part in location_parts if part) or None

        raw_description = posting.get("description")
        description = strip_html(html.unescape(raw_description)) if raw_description else None

        return LinkedInJobDetail(
            url=job_url,
            job_id=job_id,
            title=posting.get("title"),
            company=hiring_org.get("name"),
            location=location,
            employment_type=posting.get("employmentType"),
            description=description,
            posted_at=parse_datetime(posting.get("datePosted")),
            valid_through=parse_datetime(posting.get("validThrough")),
        )

    @staticmethod
    def _extract_job_id(job_url: str) -> str | None:
        match = _JOB_ID_PATTERN.search(urlsplit(job_url).path)
        return match.group(1) if match else None

    @staticmethod
    def _extract_job_posting_json_ld(page_html: str) -> dict:
        for match in _LD_JSON_PATTERN.finditer(page_html):
            try:
                data = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            if data.get("@type") == "JobPosting":
                return data
        raise ConnectorError(
            "Could not find a JobPosting JSON-LD block on the page; "
            "LinkedIn may have changed its page structure or shown a sign-in wall"
        )
