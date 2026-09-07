import asyncio
import logging

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from job_agent.logging_utils import ApiRequestLoggingMiddleware, sanitize_url
from job_agent.transport import HttpTransport


def test_inbound_json_payload_is_logged_and_remains_available(caplog) -> None:
    test_app = FastAPI()
    test_app.add_middleware(ApiRequestLoggingMiddleware)

    @test_app.post("/echo")
    async def echo(payload: dict) -> dict:
        return payload

    caplog.set_level(logging.INFO, logger="uvicorn.error")
    with TestClient(test_app) as client:
        response = client.post("/echo", json={"keywords": ["java"], "location": "London"})

    assert response.json() == {"keywords": ["java"], "location": "London"}
    assert "API_REQUEST method=POST" in caplog.text
    assert 'payload={"keywords":["java"],"location":"London"}' in caplog.text


def test_outbound_url_and_payload_are_logged_with_secrets_redacted(caplog) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["app_key"] == "super-secret"
        return httpx.Response(200, json={"ok": True}, request=request)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            transport = HttpTransport(client)
            await transport.request(
                "adzuna",
                "POST",
                "https://api.example/jobs",
                params={"app_key": "super-secret", "what": "java"},
                json={"keywords": "java", "page": 1},
            )

    caplog.set_level(logging.INFO, logger="uvicorn.error")
    asyncio.run(scenario())

    assert "OUTBOUND_REQUEST source=adzuna method=POST" in caplog.text
    assert "what=java" in caplog.text
    assert 'payload={"keywords":"java","page":1}' in caplog.text
    assert "super-secret" not in caplog.text
    assert "OUTBOUND_RESPONSE source=adzuna" in caplog.text
    assert "status=200" in caplog.text


def test_jooble_path_api_key_is_redacted() -> None:
    safe = sanitize_url("jooble", "https://uk.jooble.org/api/super-secret-key")
    assert safe == "https://uk.jooble.org/api/[REDACTED]"
