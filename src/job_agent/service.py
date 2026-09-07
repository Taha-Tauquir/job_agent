from __future__ import annotations

import asyncio

from .connectors.base import BaseConnector
from .models import SearchQuery, SourceSearchResult


class SearchService:
    def __init__(self, connectors: dict[str, BaseConnector]) -> None:
        self.connectors = connectors

    async def close(self) -> None:
        await asyncio.gather(*(connector.close() for connector in self.connectors.values()))

    async def search_all(
        self,
        query: SearchQuery,
        sources: list[str] | None = None,
        max_pages_per_source: int | None = 20,
    ) -> list[SourceSearchResult]:
        selected = sources or list(self.connectors)
        return await asyncio.gather(
            *(self._search_source(source, query, max_pages_per_source) for source in selected)
        )

    async def search_provider(
        self,
        source_prefix: str,
        query: SearchQuery,
        max_pages_per_source: int | None = 20,
    ) -> list[SourceSearchResult]:
        """Search every configured connector belonging to one provider.

        Exact source names cover one-account APIs such as `reed`. Prefix
        matching covers providers with multiple configured boards, such as
        `greenhouse:cloudflare` and `greenhouse:another-company`.
        """
        selected = [
            source
            for source in self.connectors
            if source == source_prefix or source.startswith(f"{source_prefix}:")
        ]
        if not selected:
            return [
                SourceSearchResult(
                    source=source_prefix,
                    error=f"{source_prefix} is not configured",
                )
            ]
        return await self.search_all(query, selected, max_pages_per_source)

    async def _search_source(
        self,
        source: str,
        query: SearchQuery,
        max_pages: int | None,
    ) -> SourceSearchResult:
        connector = self.connectors.get(source)
        if connector is None:
            return SourceSearchResult(source=source, error="Source is not configured")

        cursor: str | None = None
        jobs = []
        seen_job_keys: set[tuple[str, str]] = set()
        seen_cursors: set[str] = set()
        pages = 0
        total_reported: int | None = None
        try:
            while True:
                page = await connector.search(query, cursor)
                pages += 1
                for job in page.jobs:
                    key = (job.source, job.source_job_id)
                    if key not in seen_job_keys:
                        seen_job_keys.add(key)
                        jobs.append(job)
                if page.total_available is not None:
                    total_reported = page.total_available
                cursor = page.next_cursor
                if cursor is None or (max_pages is not None and pages >= max_pages):
                    break
                if cursor in seen_cursors:
                    raise RuntimeError(
                        f"{source} pagination repeated cursor {cursor}; stopped to avoid a loop"
                    )
                seen_cursors.add(cursor)
            return SourceSearchResult(
                source=source,
                jobs=jobs,
                pages_fetched=pages,
                total_reported=total_reported,
            )
        except Exception as exc:  # Result must preserve failures from other sources.
            return SourceSearchResult(
                source=source,
                jobs=jobs,
                pages_fetched=pages,
                total_reported=total_reported,
                error=str(exc),
            )
