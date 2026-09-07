import asyncio

import httpx

from job_agent.connectors import LinkedInPublicConnector
from job_agent.models import SearchQuery


HTML = """
<html><body>
  <ul class="jobs-search__results-list">
    <li>
      <div class="base-card job-search-card" data-entity-urn="urn:li:jobPosting:4444978733">
        <a class="base-card__full-link" href="https://uk.linkedin.com/jobs/view/java-engineer-4444978733?trackingId=test"></a>
        <h3 class="base-search-card__title"> Java Engineer </h3>
        <h4 class="base-search-card__subtitle"> Example Bank </h4>
        <span class="job-search-card__location"> London, United Kingdom </span>
        <time datetime="2026-09-06">1 hour ago</time>
      </div>
    </li>
  </ul>
</body></html>
"""

DETAIL_HTML = """
<html><body>
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "JobPosting",
    "identifier": {"value": "4444978733"},
    "title": "Java Engineer",
    "description": "<p>Build Java and Spring services.</p><ul><li>Design APIs</li></ul>",
    "datePosted": "2026-09-06",
    "validThrough": "2026-10-06T23:59:59Z",
    "employmentType": "FULL_TIME",
    "hiringOrganization": {"name": "Example Bank"},
    "jobLocation": {
      "address": {
        "addressLocality": "London",
        "addressRegion": "England",
        "addressCountry": "GB"
      }
    }
  }
  </script>
</body></html>
"""


def test_linkedin_public_html_is_normalized_without_tracking_parameters() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["keywords"] == "python"
        assert "currentJobId" not in request.url.params
        assert request.url.params["f_TPR"] == "r86400"
        return httpx.Response(200, text=HTML)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = LinkedInPublicConnector(
                "https://www.linkedin.com/jobs/search/?currentJobId=1&f_TPR=r86400&keywords=java",
                client,
            )
            return await connector.search(SearchQuery(keywords=["python"]))

    page = asyncio.run(scenario())
    assert page.total_available == 1
    assert page.next_cursor is None
    assert page.jobs[0].source_job_id == "4444978733"
    assert page.jobs[0].title == "Java Engineer"
    assert page.jobs[0].company == "Example Bank"
    assert page.jobs[0].job_url == "https://uk.linkedin.com/jobs/view/java-engineer-4444978733"


def test_linkedin_guest_fragment_advances_by_returned_card_count() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["start"] == "100"
        return httpx.Response(200, text=HTML)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = LinkedInPublicConnector(
                "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=java&start=0",
                client,
            )
            return await connector.search(SearchQuery(keywords=["java"]), cursor="100")

    page = asyncio.run(scenario())
    assert len(page.jobs) == 1
    assert page.next_cursor == "101"
    assert page.total_available is None


def test_linkedin_public_stops_on_sign_in_wall_without_cards() -> None:
    async def scenario():
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text="<html><body>Sign in to continue</body></html>")
        )
        async with httpx.AsyncClient(transport=transport) as client:
            connector = LinkedInPublicConnector(
                "https://www.linkedin.com/jobs/search/?keywords=java",
                client,
            )
            return await connector.search(SearchQuery(keywords=["java"]))

    try:
        asyncio.run(scenario())
    except RuntimeError as exc:
        assert "sign-in wall" in str(exc)
    else:
        raise AssertionError("Expected the connector to reject a sign-in wall")


def test_linkedin_configured_filters_are_applied_to_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        assert params["geoId"] == "101165590"
        assert params["f_TPR"] == "r604800"  # "week" alias
        assert params["f_E"] == "3,4"  # associate, mid_senior
        assert params["f_JT"] == "F"  # full_time
        assert params["f_WT"] == "2"  # remote
        assert params["sortBy"] == "DD"  # recent
        assert params["distance"] == "25"
        assert params["f_C"] == "1035"  # extra_params passthrough
        return httpx.Response(200, text=HTML)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = LinkedInPublicConnector(
                "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=java&start=0",
                client,
                geo_id="101165590",
                time_posted="week",
                experience_levels=["associate", "mid_senior"],
                job_types=["full_time"],
                workplace_types=["remote"],
                sort_by="recent",
                distance=25,
                extra_params={"f_C": "1035"},
            )
            return await connector.search(SearchQuery(keywords=["java"]))

    page = asyncio.run(scenario())
    assert len(page.jobs) == 1


def test_linkedin_query_overrides_configured_time_posted_and_remote() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        # Static config says "month" and doesn't set remote; the per-request
        # query asks for the last day and remote-only, which must win.
        assert params["f_TPR"] == "r86400"
        assert params["f_WT"] == "2"
        return httpx.Response(200, text=HTML)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = LinkedInPublicConnector(
                "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=java&start=0",
                client,
                time_posted="month",
            )
            query = SearchQuery(keywords=["java"], posted_within_days=1, remote=True)
            return await connector.search(query)

    page = asyncio.run(scenario())
    assert len(page.jobs) == 1


def test_linkedin_query_location_is_applied() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["location"] == "Manchester"
        return httpx.Response(200, text=HTML)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = LinkedInPublicConnector(
                "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=java&start=0",
                client,
            )
            return await connector.search(SearchQuery(keywords=["java"], location="Manchester"))

    page = asyncio.run(scenario())
    assert len(page.jobs) == 1


def test_linkedin_search_enriches_each_result_with_description() -> None:
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if "/jobs/view/" in request.url.path:
            return httpx.Response(200, text=DETAIL_HTML, request=request)
        return httpx.Response(200, text=HTML, request=request)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = LinkedInPublicConnector(
                "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=java&start=0",
                client,
                request_delay_seconds=0,
            )
            return await connector.search(SearchQuery(keywords=["java"]))

    page = asyncio.run(scenario())
    job = page.jobs[0]
    assert len(requested_paths) == 2
    assert "Build Java and Spring services" in (job.description or "")
    assert job.employment_type == "FULL_TIME"
    assert job.location == "London, England, GB"
    assert job.expires_at is not None
    assert job.application_deadline == job.expires_at
    assert job.provider_data["linkedin_detail"]["job_id"] == "4444978733"
