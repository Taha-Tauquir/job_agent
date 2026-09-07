import asyncio

from job_agent.api import app
from job_agent.connectors.base import BaseConnector
from job_agent.models import NormalizedJob, SearchPage, SearchQuery
from job_agent.service import SearchService


class FakeConnector(BaseConnector):
    def __init__(self, source: str) -> None:
        super().__init__()
        self.source = source

    async def search(self, query: SearchQuery, cursor: str | None = None) -> SearchPage:
        return SearchPage(
            jobs=[
                NormalizedJob(
                    source=self.source,
                    source_job_id="1",
                    title=query.keyword_text,
                    company="Example",
                    job_url="https://example.com/jobs/1",
                )
            ]
        )


class RepeatingCursorConnector(FakeConnector):
    async def search(self, query: SearchQuery, cursor: str | None = None) -> SearchPage:
        page = await super().search(query, cursor)
        return page.model_copy(update={"next_cursor": "same-page"})


def test_openapi_exposes_one_search_path_per_provider() -> None:
    paths = app.openapi()["paths"]
    expected = {
        "/api/linkedin/search",
        "/api/reed/search",
        "/api/adzuna/search",
        "/api/jooble/search",
        "/api/greenhouse/search",
        "/api/lever/search",
        "/api/ashby/search",
        "/api/smartrecruiters/search",
        "/api/sponsoredjobs/search",
    }
    assert expected <= paths.keys()


def test_provider_search_selects_all_boards_for_that_provider() -> None:
    async def scenario():
        service = SearchService(
            {
                "greenhouse:first": FakeConnector("greenhouse:first"),
                "greenhouse:second": FakeConnector("greenhouse:second"),
                "reed": FakeConnector("reed"),
            }
        )
        try:
            return await service.search_provider("greenhouse", SearchQuery(keywords=["java"]))
        finally:
            await service.close()

    results = asyncio.run(scenario())
    assert [result.source for result in results] == ["greenhouse:first", "greenhouse:second"]
    assert sum(len(result.jobs) for result in results) == 2


def test_unconfigured_provider_returns_a_clear_source_error() -> None:
    results = asyncio.run(
        SearchService({}).search_provider("adzuna", SearchQuery(keywords=["java"]))
    )
    assert results[0].source == "adzuna"
    assert results[0].error == "adzuna is not configured"


def test_repeated_provider_cursor_stops_without_duplicate_jobs() -> None:
    async def scenario():
        connector = RepeatingCursorConnector("example")
        service = SearchService({"example": connector})
        try:
            return (await service.search_all(SearchQuery(keywords=["java"])))[0]
        finally:
            await service.close()

    result = asyncio.run(scenario())
    assert result.pages_fetched == 2
    assert len(result.jobs) == 1
    assert "repeated cursor" in (result.error or "")
