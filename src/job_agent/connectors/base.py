from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import httpx

from job_agent.errors import ConnectorError
from job_agent.models import SearchPage, SearchQuery
from job_agent.transport import HttpTransport, RequestPolicy


class BaseConnector(ABC):
    source: str

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        request_policy: RequestPolicy | None = None,
    ) -> None:
        self.transport = HttpTransport(client, request_policy)
        self.client = self.transport.client

    @abstractmethod
    async def search(self, query: SearchQuery, cursor: str | None = None) -> SearchPage:
        raise NotImplementedError

    async def close(self) -> None:
        await self.transport.close()

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        return await self.transport.request(self.source, method, url, **kwargs)

    async def _json_request(self, method: str, url: str, **kwargs: Any) -> Any:
        response = await self._request(method, url, **kwargs)
        try:
            return response.json()
        except ValueError as exc:
            raise ConnectorError(f"{self.source} returned invalid JSON: {exc}") from exc
