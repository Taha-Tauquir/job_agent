from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

from job_agent.errors import ConnectorError
from job_agent.models import SearchPage, SearchQuery

from .base import BaseConnector


@dataclass(frozen=True, slots=True)
class RequestSpec:
    """Provider-neutral description of one source request."""

    method: str
    url: str
    kwargs: dict[str, Any] = field(default_factory=dict)


class RequestResponseConnector(BaseConnector):
    """Template for connectors that map one HTTP response to one job page.

    New providers normally implement only `build_request` and
    `parse_response`. Transport errors, throttling, and client lifecycle stay
    centralized in `HttpTransport`.
    """

    async def search(self, query: SearchQuery, cursor: str | None = None) -> SearchPage:
        request = self.build_request(query, cursor)
        response = await self._request(request.method, request.url, **request.kwargs)
        try:
            return self.parse_response(response, query, cursor)
        except ConnectorError:
            raise
        except Exception as exc:
            raise ConnectorError(f"{self.source} response parsing failed: {exc}") from exc

    @abstractmethod
    def build_request(self, query: SearchQuery, cursor: str | None) -> RequestSpec:
        raise NotImplementedError

    @abstractmethod
    def parse_response(
        self,
        response: httpx.Response,
        query: SearchQuery,
        cursor: str | None,
    ) -> SearchPage:
        raise NotImplementedError
