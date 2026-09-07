import asyncio

import httpx

from job_agent.connectors import LeverConnector
from job_agent.models import SearchQuery


def test_lever_normalizes_results_and_paginates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v0/postings/example"
        assert request.url.params["skip"] == "0"
        assert request.url.params["limit"] == "1"
        return httpx.Response(
            200,
            json=[
                {
                    "id": "lev-1",
                    "text": "Backend Engineer",
                    "categories": {"location": "Remote - UK", "commitment": "Full-time"},
                    "descriptionPlain": "Develop Java APIs.",
                    "workplaceType": "remote",
                    "salaryRange": {
                        "min": 55000,
                        "max": 70000,
                        "currency": "GBP",
                        "interval": "year",
                    },
                    "hostedUrl": "https://jobs.lever.co/example/lev-1",
                    "applyUrl": "https://jobs.lever.co/example/lev-1/apply",
                }
            ],
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = LeverConnector("example", "Example Co", client)
            return await connector.search(SearchQuery(keywords=["java"], page_size=1))

    page = asyncio.run(scenario())
    assert page.next_cursor == "1"
    assert page.jobs[0].salary_max == 70000
    assert page.jobs[0].remote is True
    assert page.jobs[0].apply_url.endswith("/apply")

