import asyncio

import httpx

from job_agent.connectors import SmartRecruitersConnector
from job_agent.models import SearchQuery


def test_smartrecruiters_normalizes_results_and_paginates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/companies/example/postings":
            assert request.url.params["q"] == "data"
            return httpx.Response(200, json={
                "offset": 0,
                "limit": 1,
                "totalFound": 2,
                "content": [
                    {
                        "id": "sr-1",
                        "name": "Data Engineer",
                        "company": {"name": "Example Co"},
                        "releasedDate": "2026-09-05T10:00:00Z",
                        "location": {
                            "fullLocation": "London, UK",
                            "remote": False,
                        },
                        "typeOfEmployment": {"label": "Full-time"},
                        "ref": "https://api.smartrecruiters.com/v1/companies/example/postings/sr-1",
                    }
                ],
            })
        assert request.url.path == "/v1/companies/example/postings/sr-1"
        return httpx.Response(200, json={
            "id": "sr-1",
            "name": "Data Engineer",
            "company": {"name": "Example Co"},
            "releasedDate": "2026-09-05T10:00:00Z",
            "location": {"fullLocation": "London, UK", "remote": False},
            "typeOfEmployment": {"label": "Full-time"},
            "postingUrl": "https://jobs.smartrecruiters.com/example/sr-1-data-engineer",
            "applyUrl": "https://jobs.smartrecruiters.com/example/sr-1-data-engineer?apply=true",
            "jobAd": {"sections": {"jobDescription": {"text": "<p>Build data systems.</p>"}}},
        })

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = SmartRecruitersConnector("example", client=client)
            return await connector.search(SearchQuery(keywords=["data"], page_size=1))

    page = asyncio.run(scenario())
    assert page.next_cursor == "1"
    assert page.jobs[0].company == "Example Co"
    assert page.jobs[0].job_url == "https://jobs.smartrecruiters.com/example/sr-1-data-engineer"
    assert "Build data systems" in page.jobs[0].description
    assert page.jobs[0].employment_type == "Full-time"
