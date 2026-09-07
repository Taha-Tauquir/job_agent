from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


http_logger = logging.getLogger("uvicorn.error")

_SENSITIVE_NAMES = {
    "api_key",
    "app_key",
    "access_token",
    "token",
    "password",
    "secret",
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
}


def redact(value: Any, key: str | None = None) -> Any:
    """Return a log-safe representation while preserving useful request data."""
    if key and key.casefold().replace("-", "_") in {
        name.replace("-", "_") for name in _SENSITIVE_NAMES
    }:
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(item_key): redact(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return f"<{type(value).__name__}>"


def sanitize_url(source: str, url: str, params: Any = None) -> str:
    request_url = httpx.Request("GET", url, params=params).url
    parts = urlsplit(str(request_url))
    safe_query = [
        (key, "[REDACTED]" if key.casefold() in _SENSITIVE_NAMES else value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    path = parts.path
    if source == "jooble" and "/api/" in path:
        path = f"{path.split('/api/', 1)[0]}/api/[REDACTED]"
    return urlunsplit((parts.scheme, parts.netloc, path, urlencode(safe_query), ""))


def compact_json(value: Any, *, limit: int = 8000) -> str:
    rendered = json.dumps(redact(value), ensure_ascii=False, separators=(",", ":"), default=str)
    if len(rendered) <= limit:
        return rendered
    return f"{rendered[:limit]}…[truncated {len(rendered) - limit} chars]"


class ApiRequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log inbound method, full URL, and JSON/body payload without consuming it."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        body = await request.body()
        payload: Any = None
        if body:
            content_type = request.headers.get("content-type", "")
            if "application/json" in content_type:
                try:
                    payload = json.loads(body)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    payload = "[invalid JSON body]"
            else:
                payload = f"[non-JSON body: {len(body)} bytes]"
        http_logger.info(
            "API_REQUEST method=%s url=%s payload=%s",
            request.method,
            request.url,
            compact_json(payload),
        )
        return await call_next(request)
