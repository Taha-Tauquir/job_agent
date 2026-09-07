import asyncio

import httpx

from job_agent.connectors import AshbyConnector
from job_agent.models import SearchQuery


def test_ashby_normalizes_and_filters_company_board() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/posting-api/job-board/example"
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": "ash-1",
                        "title": "Machine Learning Engineer",
                        "location": "Remote - Europe",
                        "descriptionPlain": "Build Python ML systems.",
                        "employmentType": "FullTime",
                        "isRemote": True,
                        "isListed": True,
                        "publishedAt": "2026-09-05T09:30:00Z",
                        "jobUrl": "https://jobs.ashbyhq.com/example/ash-1",
                        "applyUrl": "https://jobs.ashbyhq.com/example/ash-1/application",
                        "compensation": {
                            "minValue": 60000,
                            "maxValue": 80000,
                            "currencyCode": "GBP",
                            "interval": "YEAR",
                        },
                    },
                    {
                        "id": "ash-2",
                        "title": "Sales Executive",
                        "location": "London",
                        "isListed": True,
                        "jobUrl": "https://jobs.ashbyhq.com/example/ash-2",
                    },
                ]
            },
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = AshbyConnector("example", "Example Co", client)
            return await connector.search(SearchQuery(keywords=["python"]))

    page = asyncio.run(scenario())
    assert page.total_available == 2
    assert len(page.jobs) == 1
    assert page.jobs[0].salary_min == 60000
    assert page.jobs[0].employment_type == "FullTime"

