import asyncio

import httpx

from job_agent.connectors import GreenhouseConnector
from job_agent.models import SearchQuery


def test_greenhouse_normalizes_and_filters_company_board() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/boards/example/jobs"
        assert request.url.params["content"] == "true"
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": 1,
                        "title": "Platform Engineer",
                        "location": {"name": "Remote, UK"},
                        "content": "<p>Work with Python services.</p>",
                        "updated_at": "2026-09-05T11:00:00Z",
                        "absolute_url": "https://boards.greenhouse.io/example/jobs/1",
                    },
                    {
                        "id": 2,
                        "title": "Accountant",
                        "location": {"name": "London"},
                        "absolute_url": "https://boards.greenhouse.io/example/jobs/2",
                    },
                ],
                "meta": {"total": 2},
            },
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = GreenhouseConnector("example", "Example Co", client=client)
            return await connector.search(SearchQuery(keywords=["python"]))

    page = asyncio.run(scenario())
    assert page.total_available == 2
    assert len(page.jobs) == 1
    assert page.jobs[0].company == "Example Co"
    assert page.jobs[0].remote is True

