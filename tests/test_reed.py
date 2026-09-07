import asyncio
from datetime import datetime, timedelta, timezone

import httpx

from job_agent.connectors import ReedConnector
from job_agent.models import SearchQuery


def test_reed_normalizes_results_and_paginates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["keywords"] == "java"
        assert request.url.params["locationName"] == "London"
        assert request.headers["authorization"].startswith("Basic ")
        return httpx.Response(
            200,
            json={
                "totalResults": 3,
                "results": [
                    {
                        "jobId": 101,
                        "jobTitle": "Java Developer",
                        "employerName": "Example Ltd",
                        "locationName": "London",
                        "minimumSalary": 45000,
                        "maximumSalary": 60000,
                        "date": "2026-09-05T10:00:00Z",
                        "jobUrl": "https://www.reed.co.uk/jobs/101",
                    },
                    {
                        "jobId": 102,
                        "jobTitle": "Backend Engineer",
                        "employerName": "Second Ltd",
                        "locationName": "London",
                        "jobUrl": "https://www.reed.co.uk/jobs/102",
                    },
                ],
            },
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await ReedConnector("secret", client).search(
                SearchQuery(keywords=["java"], location="London", page_size=2)
            )

    page = asyncio.run(scenario())
    assert page.next_cursor == "2"
    assert page.total_available == 3
    assert len(page.jobs) == 2
    assert page.jobs[0].source == "reed"
    assert page.jobs[0].salary_min == 45000
    assert page.jobs[0].salary_currency == "GBP"


def test_reed_filters_results_by_posted_hours_locally() -> None:
    now = datetime.now(timezone.utc)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "totalResults": 2,
                "results": [
                    {
                        "jobId": 201,
                        "jobTitle": "Recent Java Developer",
                        "employerName": "Recent Ltd",
                        "locationName": "London",
                        "date": (now - timedelta(hours=3)).isoformat(),
                        "jobUrl": "https://www.reed.co.uk/jobs/201",
                    },
                    {
                        "jobId": 202,
                        "jobTitle": "Older Java Developer",
                        "employerName": "Older Ltd",
                        "locationName": "London",
                        "date": (now - timedelta(hours=30)).isoformat(),
                        "jobUrl": "https://www.reed.co.uk/jobs/202",
                    },
                ],
            },
            request=request,
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await ReedConnector("secret", client).search(
                SearchQuery(keywords=["java"], posted_within_hours=24, page_size=10)
            )

    page = asyncio.run(scenario())
    assert [job.source_job_id for job in page.jobs] == ["201"]
    assert page.total_available == 2
