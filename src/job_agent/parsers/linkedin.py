from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from lxml import html

from job_agent.errors import ConnectorError
from job_agent.models import NormalizedJob
from job_agent.utils import fallback_job_id, infer_remote, parse_datetime


class LinkedInSearchParser:
    """Convert LinkedIn public search HTML into provider-neutral jobs."""

    def parse(
        self,
        page_html: str,
        *,
        source: str,
        configured_search_url: str,
        allow_empty: bool,
    ) -> list[NormalizedJob]:
        if not page_html.strip():
            if allow_empty:
                return []
            raise ConnectorError("LinkedIn returned an empty search page")

        document = html.fromstring(page_html)
        cards = document.xpath('//li[.//div[contains(@class, "base-card")]]')
        if not cards:
            page_text = " ".join(document.text_content().split()).casefold()
            if "sign in" in page_text or "join now" in page_text:
                raise ConnectorError("LinkedIn returned a sign-in wall without public job cards")
            if allow_empty and not page_text:
                return []
            raise ConnectorError("LinkedIn returned no recognizable public job cards")

        return [
            self._normalize(card, source=source, configured_search_url=configured_search_url)
            for card in cards
        ]

    def _normalize(self, card, *, source: str, configured_search_url: str) -> NormalizedJob:
        title = self._text(card, './/h3[contains(@class, "base-search-card__title")]')
        company = self._text(card, './/h4[contains(@class, "base-search-card__subtitle")]')
        location = self._text(card, './/span[contains(@class, "job-search-card__location")]')
        posted = self._attribute(card, ".//time", "datetime")
        raw_url = self._attribute(card, './/a[contains(@class, "base-card__full-link")]', "href")
        entity = self._attribute(card, './/div[contains(@class, "base-card")]', "data-entity-urn")
        match = re.search(r"jobPosting:(\d+)", entity or "")
        job_id = match.group(1) if match else fallback_job_id(raw_url, title, company)
        job_url = self._canonical_url(raw_url)
        return NormalizedJob(
            source=source,
            source_job_id=job_id,
            title=title or "Untitled job",
            company=company or "Unknown employer",
            location=location,
            remote=infer_remote(location),
            posted_at=parse_datetime(posted),
            job_url=job_url,
            apply_url=job_url,
            raw={
                "entity_urn": entity,
                "search_result_url": raw_url,
                "configured_search_url": configured_search_url,
            },
        )

    @staticmethod
    def _text(card, xpath: str) -> str | None:
        nodes = card.xpath(xpath)
        return " ".join(nodes[0].text_content().split()) if nodes else None

    @staticmethod
    def _attribute(card, xpath: str, name: str) -> str | None:
        nodes = card.xpath(xpath)
        return nodes[0].get(name) if nodes else None

    @staticmethod
    def _canonical_url(value: str | None) -> str:
        if not value:
            raise ConnectorError("LinkedIn job card did not include a link")
        parts = urlsplit(value)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
