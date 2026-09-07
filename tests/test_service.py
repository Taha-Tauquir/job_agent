import asyncio

import httpx

from job_agent.connectors import LeverConnector
from job_agent.models import SearchQuery
from job_agent.service import SearchService


def test_service_follows_connectors_until_last_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        skip = int(request.url.params["skip"])
        if skip == 0:
            rows = [
                {
                    "id": "one",
                    "text": "Engineer One",
                    "categories": {},
                    "hostedUrl": "https://jobs.test/one",
                }
            ]
        else:
            rows = []
        return httpx.Response(200, json=rows)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = LeverConnector("example", client=client)
            service = SearchService({connector.source: connector})
            return await service.search_all(SearchQuery(page_size=1), max_pages_per_source=None)

    results = asyncio.run(scenario())
    assert results[0].error is None
    assert results[0].pages_fetched == 2
    assert len(results[0].jobs) == 1


def test_service_preserves_unknown_source_as_error() -> None:
    results = asyncio.run(SearchService({}).search_all(SearchQuery(), ["missing"]))
    assert results[0].error == "Source is not configured"

