from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from .errors import ConnectorError
from .logging_utils import compact_json, sanitize_url


logger = logging.getLogger("uvicorn.error")


@dataclass(frozen=True, slots=True)
class RequestPolicy:
    """Reusable pacing and rate-limit behavior for one source client."""

    minimum_interval_seconds: float = 0.0
    rate_limit_retries: int = 0
    backoff_base_seconds: float = 2.0

    def __post_init__(self) -> None:
        if self.minimum_interval_seconds < 0:
            raise ValueError("minimum_interval_seconds cannot be negative")
        if self.rate_limit_retries < 0:
            raise ValueError("rate_limit_retries cannot be negative")
        if self.backoff_base_seconds < 0:
            raise ValueError("backoff_base_seconds cannot be negative")


class HttpTransport:
    """Owns HTTP lifecycle, pacing, status handling, and 429 backoff."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        policy: RequestPolicy | None = None,
    ) -> None:
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(30),
            follow_redirects=True,
            headers={"User-Agent": "PersonalJobAgent/0.2"},
        )
        self.policy = policy or RequestPolicy()
        self._last_request_at: float | None = None
        self._pacing_lock = asyncio.Lock()

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def request(self, source: str, method: str, url: str, **kwargs: Any) -> httpx.Response:
        attempt = 0
        while True:
            await self._wait_for_request_slot()
            safe_url = sanitize_url(source, url, kwargs.get("params"))
            request_payload = kwargs.get("json", kwargs.get("data"))
            started_at = time.monotonic()
            logger.info(
                "OUTBOUND_REQUEST source=%s method=%s url=%s payload=%s attempt=%s",
                source,
                method.upper(),
                safe_url,
                compact_json(request_payload),
                attempt + 1,
            )
            try:
                response = await self.client.request(method, url, **kwargs)
                logger.info(
                    "OUTBOUND_RESPONSE source=%s method=%s url=%s status=%s duration_ms=%.1f",
                    source,
                    method.upper(),
                    safe_url,
                    response.status_code,
                    (time.monotonic() - started_at) * 1000,
                )
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429 and attempt < self.policy.rate_limit_retries:
                    attempt += 1
                    retry_after = self._parse_retry_after(exc.response.headers.get("retry-after"))
                    wait_for = retry_after
                    if wait_for is None:
                        wait_for = self.policy.backoff_base_seconds * (2**attempt)
                    logger.warning(
                        "OUTBOUND_RATE_LIMIT source=%s url=%s retry_in_seconds=%.2f next_attempt=%s",
                        source,
                        safe_url,
                        wait_for,
                        attempt + 1,
                    )
                    await asyncio.sleep(wait_for)
                    continue
                detail = exc.response.text[:300].replace("\n", " ")
                raise ConnectorError(
                    f"{source} returned HTTP {exc.response.status_code}: {detail}"
                ) from exc
            except httpx.HTTPError as exc:
                logger.error(
                    "OUTBOUND_FAILURE source=%s method=%s url=%s duration_ms=%.1f error=%s",
                    source,
                    method.upper(),
                    safe_url,
                    (time.monotonic() - started_at) * 1000,
                    exc,
                )
                raise ConnectorError(f"{source} request failed: {exc}") from exc

    async def _wait_for_request_slot(self) -> None:
        async with self._pacing_lock:
            now = time.monotonic()
            if self._last_request_at is not None:
                remaining = self.policy.minimum_interval_seconds - (now - self._last_request_at)
                if remaining > 0:
                    await asyncio.sleep(remaining)
            self._last_request_at = time.monotonic()

    @staticmethod
    def _parse_retry_after(value: str | None) -> float | None:
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
            except (TypeError, ValueError):
                return None
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
