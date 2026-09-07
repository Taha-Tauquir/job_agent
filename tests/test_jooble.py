import asyncio
import json

import httpx

from job_agent.connectors import JoobleConnector
from job_agent.models import SearchQuery


def test_jooble_normalizes_results_and_paginates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path == "/api/key-123"
        assert body["keywords"] == "data engineer"
        assert body["location"] == "Birmingham"
        return httpx.Response(
            200,
            json={
                "totalCount": 2,
                "jobs": [
                    {
                        "id": 800,
                        "title": "Data Engineer",
                        "company": "Example Data",
                        "location": "Birmingham",
                        "snippet": "<b>SQL</b> and Python",
                        "salary": "£50,000–£60,000",
                        "type": "Full-time",
                        "updated": "2026-09-05T12:00:00Z",
                        "link": "https://example.test/jobs/800",
                    }
                ],
            },
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = JoobleConnector("key-123", "https://uk.jooble.org", client)
            return await connector.search(
                SearchQuery(keywords=["data", "engineer"], location="Birmingham", page_size=1)
            )

    page = asyncio.run(scenario())
    assert page.next_cursor == "2"
    assert page.jobs[0].description == "SQL and Python"
    assert page.jobs[0].salary_text == "£50,000–£60,000"

