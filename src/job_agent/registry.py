from __future__ import annotations

import os
from collections.abc import Iterable
from urllib.parse import parse_qsl

from dotenv import load_dotenv

from .connectors import (
    AdzunaConnector,
    AshbyConnector,
    GreenhouseConnector,
    JoobleConnector,
    LeverConnector,
    LinkedInPublicConnector,
    ReedConnector,
    SmartRecruitersConnector,
    SponsoredJobsConnector,
)
from .connectors.base import BaseConnector


def _configured_boards(value: str | None) -> Iterable[tuple[str, str | None]]:
    for item in (value or "").split(","):
        item = item.strip()
        if not item:
            continue
        identifier, separator, display_name = item.partition("|")
        yield identifier.strip(), display_name.strip() if separator else None


def _csv_list(value: str | None) -> list[str] | None:
    if not value:
        return None
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or None


def _linkedin_extra_params(value: str | None) -> dict[str, str]:
    if not value:
        return {}
    return dict(parse_qsl(value.strip().lstrip("?")))


def build_connectors_from_env() -> dict[str, BaseConnector]:
    """Build only connectors enabled by the local environment configuration."""
    load_dotenv()
    connectors: list[BaseConnector] = []

    if reed_key := os.getenv("REED_API_KEY"):
        connectors.append(ReedConnector(reed_key))
    if adzuna_id := os.getenv("ADZUNA_APP_ID"):
        adzuna_key = os.getenv("ADZUNA_APP_KEY")
        if adzuna_key:
            connectors.append(
                AdzunaConnector(adzuna_id, adzuna_key, os.getenv("ADZUNA_COUNTRY", "gb"))
            )
    if jooble_key := os.getenv("JOOBLE_API_KEY"):
        connectors.append(
            JoobleConnector(jooble_key, os.getenv("JOOBLE_BASE_URL", "https://uk.jooble.org"))
        )

    for board, company in _configured_boards(os.getenv("GREENHOUSE_BOARDS")):
        connectors.append(GreenhouseConnector(board, company))
    for site, company in _configured_boards(os.getenv("LEVER_SITES")):
        connectors.append(LeverConnector(site, company))
    for board, company in _configured_boards(os.getenv("ASHBY_BOARDS")):
        connectors.append(AshbyConnector(board, company))
    for identifier, company in _configured_boards(os.getenv("SMARTRECRUITERS_COMPANIES")):
        connectors.append(SmartRecruitersConnector(identifier, company))

    if linkedin_url := os.getenv("LINKEDIN_PUBLIC_SEARCH_URL"):
        distance = os.getenv("LINKEDIN_DISTANCE")
        connectors.append(
            LinkedInPublicConnector(
                linkedin_url,
                geo_id=os.getenv("LINKEDIN_GEO_ID"),
                time_posted=os.getenv("LINKEDIN_TIME_POSTED"),
                experience_levels=_csv_list(os.getenv("LINKEDIN_EXPERIENCE_LEVELS")),
                job_types=_csv_list(os.getenv("LINKEDIN_JOB_TYPES")),
                workplace_types=_csv_list(os.getenv("LINKEDIN_WORKPLACE_TYPES")),
                sort_by=os.getenv("LINKEDIN_SORT_BY"),
                distance=int(distance) if distance else None,
                extra_params=_linkedin_extra_params(os.getenv("LINKEDIN_EXTRA_PARAMS")),
            )
        )

    if os.getenv("SPONSOREDJOBS_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
        connectors.append(
            SponsoredJobsConnector(
                os.getenv(
                    "SPONSOREDJOBS_SEARCH_URL",
                    "https://sponsoredjobs.co.uk/jobs/sector/it?per_page=100",
                ),
                request_delay_seconds=float(
                    os.getenv("SPONSOREDJOBS_REQUEST_DELAY_SECONDS", "0.25")
                ),
            )
        )

    return {connector.source: connector for connector in connectors}
