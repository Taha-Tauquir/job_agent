from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Request

from .models import ProviderSearchRequest, SearchQuery, SearchResponse


# Public route name -> internal connector source prefix. A provider can own
# several configured boards (`greenhouse:*`), all returned by its endpoint.
PROVIDER_ROUTES = {
    "linkedin": "linkedin-public",
    "reed": "reed",
    "adzuna": "adzuna",
    "jooble": "jooble",
    "greenhouse": "greenhouse",
    "lever": "lever",
    "ashby": "ashby",
    "smartrecruiters": "smartrecruiters",
    "sponsoredjobs": "sponsoredjobs",
}

router = APIRouter(prefix="/api", tags=["provider searches"])


def _make_search_endpoint(
    route_name: str,
    source_prefix: str,
) -> Callable[..., SearchResponse]:
    async def provider_search(
        body: ProviderSearchRequest,
        request: Request,
    ) -> SearchResponse:
        query = SearchQuery(**body.model_dump(exclude={"max_pages_per_source"}))
        results = await request.app.state.search_service.search_provider(
            source_prefix,
            query,
            body.max_pages_per_source,
        )
        return SearchResponse(query=query, results=results)

    provider_search.__name__ = f"search_{route_name}"
    provider_search.__doc__ = f"Search only the configured {route_name} connector(s)."
    return provider_search


for _route_name, _source_prefix in PROVIDER_ROUTES.items():
    router.add_api_route(
        f"/{_route_name}/search",
        _make_search_endpoint(_route_name, _source_prefix),
        methods=["POST"],
        response_model=SearchResponse,
        summary=f"Search {_route_name.title()}",
    )
