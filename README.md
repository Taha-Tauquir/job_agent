# Personal Job Agent

This first implementation provides a local FastAPI service and normalized source
connectors for Reed, Adzuna, Jooble, Greenhouse, Lever, Ashby, and
SmartRecruiters, plus the public Sponsored Jobs IT board.

Connectors share one transport policy for HTTP lifecycle, pacing, and rate-limit
handling. Request construction and response parsing are separate, so a new job
board can be added without changing the search service or its API response.
See [Adding another job-board connector](docs/ADDING_A_CONNECTOR.md).

An experimental `linkedin-public` connector can parse public HTML batches from
a configured LinkedIn search URL. The guest fragment URL supports offset-based
pagination. It is not a supported LinkedIn API, cannot guarantee every result,
and intentionally stops on HTTP errors or a sign-in wall rather than attempting
to bypass access controls. Review LinkedIn's terms before enabling recurring use.
Each search result is automatically enriched from its public job-detail page,
so `/api/linkedin/search`, `/api/search`, and scheduled runs return the complete
description, employment type, detail location, posted date, and expiry in the
same normalized job object. Per-job enrichment failures are exposed as
`provider_data.detail_error` without discarding the listing.

## Setup

```powershell
cd C:\Users\Hp\Desktop\ProjectCV\job-agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
Copy-Item .env.example .env
```

Add API keys and company board identifiers to `.env`. Public boards are written
as comma-separated IDs, optionally followed by `|Display Name`:

```dotenv
GREENHOUSE_BOARDS=cloudflare|Cloudflare
LEVER_SITES=palantir|Palantir
ASHBY_BOARDS=ashby|Ashby
SMARTRECRUITERS_COMPANIES=smartrecruiters|SmartRecruiters
```

## Run tests

```powershell
python -m pytest
python scripts/live_smoke.py
```

The live smoke test always checks sample public ATS boards. Reed, Adzuna, and
Jooble are added when their environment variables are configured.

## Start the local API

```powershell
uvicorn job_agent.api:app --app-dir src --reload
```

Open `http://127.0.0.1:8000/docs` for the interactive API documentation.

## Request logging

The server logs each inbound API URL and JSON body, then every outbound provider
request with its source, resolved URL, payload, attempt number, response status,
and duration. API keys, authorization values, cookies, tokens, passwords, and
Jooble's URL-embedded key are redacted automatically.

```text
INFO: API_REQUEST method=POST url=http://127.0.0.1:8000/api/linkedin/search payload={"keywords":["java"]}
INFO: OUTBOUND_REQUEST source=linkedin-public method=GET url=https://www.linkedin.com/... payload=null attempt=1
INFO: OUTBOUND_RESPONSE source=linkedin-public method=GET url=https://www.linkedin.com/... status=200 duration_ms=494.3
```

Alternatively, import
[`postman/Personal Job Agent.postman_collection.json`](postman/Personal%20Job%20Agent.postman_collection.json)
into Postman. It contains every endpoint, uses a collection-level `baseUrl`,
and saves the ID returned by Create Schedule for the other schedule requests.
An automated test fails whenever an API route is added without updating this
collection.

Example request:

```json
{
  "keywords": ["java", "spring"],
  "location": "London",
  "country": "gb",
  "page_size": 20,
  "max_pages_per_source": 5
}
```

Each provider also has its own endpoint. The request body is the same except
that `sources` is omitted because the URL selects the provider:

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/linkedin/search `
  -H "Content-Type: application/json" `
  -d '{"keywords":["java"],"location":"United Kingdom","max_pages_per_source":2}'
```

Available paths are `/api/linkedin/search`, `/api/reed/search`,
`/api/adzuna/search`, `/api/jooble/search`, `/api/greenhouse/search`,
`/api/lever/search`, `/api/ashby/search`, and
`/api/smartrecruiters/search`, and `/api/sponsoredjobs/search`. ATS endpoints search every configured company
board for that provider. `/api/search` remains available for combined searches.

The Sponsored Jobs connector enriches every listing from its detail page. Its
response includes sponsorship status, visa routes, SOC code, going-rate status,
salary range, sector, workplace type, posted/deadline dates, the complete plain
text and HTML description, external apply URL, original source, employer
website, and the site's sponsor-licence disclaimer.

Sponsored Jobs is rendered by Next.js. The connector reads the complete
structured `filteredJobs` dataset embedded in the page rather than relying on
the visible server-rendered card subset, which can be partial or duplicated.
HTML card parsing remains available as a fallback if that structured payload is
not present.

### Importing the Postman collection

In Postman, select **Import → Files**, then choose the actual
`Personal Job Agent.postman_collection.json` file from the `postman` directory.
Do not paste its filesystem path into the raw-text or collection-runner data
import box; those inputs expect a cURL command or iteration data, not a
collection file.

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/sponsoredjobs/search `
  -H "Content-Type: application/json" `
  -d '{"keywords":["java"],"max_pages_per_source":1}'
```

`max_pages_per_source` limits a foreground test. Set it to `null` to follow a
source until it reports no next page. Normal background runs should still obey
each provider's documented quotas and rate limits.

Use either `posted_within_hours` or `posted_within_days` to filter by posting
age; hours takes precedence if both are provided. LinkedIn sends this filter to
its search endpoint. Reed's documented Jobseeker Search API has no posting-age
parameter, so Reed pages are filtered locally by each result's `date`; increase
`max_pages_per_source` when you need a more exhaustive Reed scan.
