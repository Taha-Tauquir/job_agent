from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from job_agent.connectors import (  # noqa: E402
    AdzunaConnector,
    AshbyConnector,
    GreenhouseConnector,
    JoobleConnector,
    LeverConnector,
    ReedConnector,
    SmartRecruitersConnector,
)
from job_agent.models import SearchQuery  # noqa: E402


async def main() -> int:
    connectors = [
        GreenhouseConnector("cloudflare", "Cloudflare", include_content=False),
        LeverConnector("palantir", "Palantir"),
        AshbyConnector("ashby", "Ashby"),
        SmartRecruitersConnector("smartrecruiters", "SmartRecruiters"),
    ]
    if os.getenv("REED_API_KEY"):
        connectors.append(ReedConnector(os.environ["REED_API_KEY"]))
    if os.getenv("ADZUNA_APP_ID") and os.getenv("ADZUNA_APP_KEY"):
        connectors.append(
            AdzunaConnector(
                os.environ["ADZUNA_APP_ID"],
                os.environ["ADZUNA_APP_KEY"],
                os.getenv("ADZUNA_COUNTRY", "gb"),
            )
        )
    if os.getenv("JOOBLE_API_KEY"):
        connectors.append(
            JoobleConnector(
                os.environ["JOOBLE_API_KEY"],
                os.getenv("JOOBLE_BASE_URL", "https://uk.jooble.org"),
            )
        )

    failed = False
    query = SearchQuery(page_size=2)
    try:
        for connector in connectors:
            try:
                page = await connector.search(query)
                sample = page.jobs[0] if page.jobs else None
                print(f"{connector.source}: {len(page.jobs)} jobs returned")
                if sample:
                    print(f"  {sample.title} | {sample.company} | {sample.location}")
                    print(f"  {sample.job_url}")
                else:
                    failed = True
                    print("  ERROR: source returned no jobs")
            except Exception as exc:
                failed = True
                print(f"{connector.source}: ERROR: {exc}")
    finally:
        await asyncio.gather(*(connector.close() for connector in connectors))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

