import asyncio
import json

import httpx

from job_agent.connectors import SponsoredJobsConnector
from job_agent.models import SearchQuery
from job_agent.parsers import SponsoredJobsParser


LISTING_HTML = """
<main>
  <div>Showing 1 of 2 roles</div>
  <div class="group relative">
    <a href="/jobs/lead-cyber-security-architect-at-example">
      <h2>Lead Cyber Security Architect</h2>
      <span style="letter-spacing: 0.04em">Sponsors visa</span>
      <div class="text-brand-text-muted text-sm">Example Agency</div>
      <div class="flex flex-wrap gap-1.5">
        <span style="letter-spacing: 0.04em">SOC 2135</span>
        <span style="letter-spacing: 0.04em">skilled_worker</span>
        <span style="letter-spacing: 0.04em">Above going rate</span>
      </div>
      <div class="flex gap-x-2 gap-y-1">
        <span>Full-time</span><span>•</span><span>£56k-71k/year</span>
        <span>•</span><span>London</span>
        <time datetime="2026-09-02 00:00:00+00"></time>
      </div>
    </a>
  </div>
  <a aria-label="Go to next page" class="disabled:pointer-events-none" href="?page=2">Next</a>
</main>
"""

DETAIL_HTML = """
<main>
  <section><div><h1>Lead Cyber Security Architect</h1>
    <div><div>Example Agency</div><div><a href="/jobs/sector/it">IT</a></div></div>
    <span style="letter-spacing:0.04em">Sponsors visa</span>
    <span style="letter-spacing:0.04em">Full-time</span>
    <a href="/jobs/visa-route/skilled-worker">Skilled Worker</a>
    <a href="https://apply.example/job/123">Apply on company site</a>
  </div></section>
  <article><div class="job-prose"><h2>Job summary</h2><p>Cyber security and Java role.</p>
    <h3>Employer's website</h3><p><a href="https://example.org">Example</a></p>
  </div><p role="note">Employer holds an A-rated sponsor licence; verify sponsorship.</p></article>
  <section><h2>Job Details</h2><div>
    <div><span>Date Posted</span><span>Sep 2, 2026 (4 days ago)</span></div>
    <div><span>Application Deadline</span><span>16 September 2026</span></div>
    <div><span>Job Location</span><span>Remote</span></div>
    <div><span>Workplace Type</span><span>Remote</span></div>
    <div><span>Salary</span><span>£56k-71k/year</span></div>
    <div><span>Job Source</span><span><a href="https://apply.example/job/123">Example Jobs ↗</a></span></div>
    <div><span>Sponsorship</span><span>Yes · Skilled Worker</span></div>
  </div></section>
  <footer><a href="/jobs/visa-route/health-and-care">Health &amp; Care Visa</a></footer>
</main>
"""


def test_sponsoredjobs_parser_preserves_listing_and_detail_fields() -> None:
    parser = SponsoredJobsParser()
    jobs, cursor, total = parser.parse_listing(
        LISTING_HTML,
        page_url="https://sponsoredjobs.co.uk/jobs/sector/it?per_page=100",
        source="sponsoredjobs",
    )
    job = parser.parse_detail(DETAIL_HTML, job=jobs[0])

    assert cursor == "2"
    assert total == 2
    assert job.salary_min == 56_000
    assert job.salary_max == 71_000
    assert job.salary_currency == "GBP"
    assert job.sponsorship_status == "Yes · Skilled Worker"
    assert job.visa_routes == ["Skilled Worker"]
    assert job.soc_code == "SOC 2135"
    assert job.going_rate_status == "Above going rate"
    assert job.sector == "IT"
    assert job.workplace_type == "Remote"
    assert job.remote is True
    assert job.application_deadline is not None
    assert job.expires_at == job.application_deadline
    assert job.apply_url == "https://apply.example/job/123"
    assert "Cyber security and Java role" in (job.description or "")
    assert "job-prose" in (job.description_html or "")
    assert job.provider_data["job_source"] == "Example Jobs ↗"
    assert job.provider_data["employer_website"] == "https://example.org"
    assert "A-rated sponsor licence" in job.provider_data["sponsor_licence_note"]


def test_sponsoredjobs_prefers_complete_embedded_nextjs_dataset() -> None:
    rows = [
        {
            "id": f"job-{index}",
            "slug": f"job-{index}-at-example",
            "title": f"Engineer {index}",
            "company": "Example",
            "type": "Full-time",
            "salary": {"min": 50000, "max": 70000, "currency": "GBP", "unit": "year"},
            "posted_date": "2026-09-02 00:00:00+00",
            "valid_through": "2026-09-30 23:59:59+00",
            "apply_url": f"https://apply.example/{index}",
            "workplace_type": "Remote",
            "location_text": "United Kingdom",
            "sponsorship_tier": "verified",
            "soc_code": "2134",
            "visa_route": "skilled_worker",
            "salary_meets_threshold": True,
            "industry": "IT",
        }
        for index in range(3)
    ]
    flight_chunk = '0:{"filteredJobs":' + json.dumps(rows) + ',"other":"data"}'
    flight_entry = json.dumps([1, flight_chunk])
    page_html = f"""
      <html><body>
        <div class="group"><a href="/jobs/duplicate"><h2>Only rendered card</h2></a></div>
        <script>self.__next_f.push({flight_entry})</script>
      </body></html>
    """

    jobs, cursor, total = SponsoredJobsParser().parse_listing(
        page_html,
        page_url="https://sponsoredjobs.co.uk/jobs/sector/it?per_page=100",
        source="sponsoredjobs",
    )

    assert len(jobs) == 3
    assert total == 3
    assert cursor is None
    assert jobs[0].source_job_id == "job-0"
    assert jobs[0].sponsorship_status == "Sponsors visa"
    assert jobs[0].provider_data["listing_record"]["slug"] == "job-0-at-example"


def test_sponsoredjobs_connector_fetches_details_and_filters_after_enrichment() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/jobs/sector/it":
            return httpx.Response(200, text=LISTING_HTML, request=request)
        return httpx.Response(200, text=DETAIL_HTML, request=request)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = SponsoredJobsConnector(client=client, request_delay_seconds=0)
            return await connector.search(SearchQuery(keywords=["Java"], remote=True))

    page = asyncio.run(scenario())
    assert len(page.jobs) == 1
    assert page.jobs[0].description is not None
    assert page.next_cursor == "2"
