import asyncio

import httpx

from job_agent.errors import ConnectorError
from job_agent.transport import HttpTransport, RequestPolicy


def test_transport_retries_rate_limit_and_returns_response() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, request=request)
        return httpx.Response(200, json={"ok": True}, request=request)

    async def scenario() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            transport = HttpTransport(client, RequestPolicy(rate_limit_retries=1))
            return await transport.request("test-source", "GET", "https://example.com/jobs")

    response = asyncio.run(scenario())
    assert attempts == 2
    assert response.json() == {"ok": True}


def test_transport_wraps_http_errors_with_source_name() -> None:
    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(503, request=request))
        ) as client:
            transport = HttpTransport(client)
            await transport.request("example-board", "GET", "https://example.com/jobs")

    try:
        asyncio.run(scenario())
    except ConnectorError as exc:
        assert "example-board returned HTTP 503" in str(exc)
    else:
        raise AssertionError("Expected an HTTP error to be normalized")
