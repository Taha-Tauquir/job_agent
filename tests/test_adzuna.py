import asyncio

import httpx

from job_agent.connectors import AdzunaConnector
from job_agent.models import SearchQuery


def test_adzuna_normalizes_results_and_paginates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/jobs/gb/search/1")
        assert request.url.params["what"] == "python developer"
        assert request.url.params["app_id"] == "app-id"
        return httpx.Response(
            200,
            json={
                "count": 3,
                "results": [
                    {
                        "id": "adz-1",
                        "title": "Python Developer",
                        "company": {"display_name": "Example Plc"},
                        "location": {"display_name": "Manchester"},
                        "description": "<p>Build useful services.</p>",
                        "salary_min": 50000,
                        "salary_max": 65000,
                        "created": "2026-09-05T09:00:00Z",
                        "redirect_url": "https://example.test/jobs/adz-1",
                    },
                    {
                        "id": "adz-2",
                        "title": "Senior Python Developer",
                        "company": {"display_name": "Example Two"},
                        "location": {"display_name": "Manchester"},
                        "redirect_url": "https://example.test/jobs/adz-2",
                    },
                ],
            },
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = AdzunaConnector("app-id", "app-key", client=client)
            return await connector.search(
                SearchQuery(keywords=["python", "developer"], location="Manchester", page_size=2)
            )

    page = asyncio.run(scenario())
    assert page.next_cursor == "2"
    assert page.jobs[0].description == "Build useful services."
    assert page.jobs[0].company == "Example Plc"

