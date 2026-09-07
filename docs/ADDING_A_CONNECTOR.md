# Adding another job-board connector

The application now separates every integration into four concerns:

1. **Configuration** maps friendly settings and `SearchQuery` fields to a provider's parameters.
2. **Connector** builds a request and controls provider-specific pagination.
3. **Parser** converts the provider's JSON or HTML into `NormalizedJob` objects.
4. **Transport** owns client lifecycle, pacing, HTTP errors, and `429` retries.

LinkedIn is the reference implementation:

- `connectors/linkedin_options.py` — filter and query-parameter mapping
- `connectors/linkedin_public.py` — request and pagination adapter
- `parsers/linkedin.py` — response normalization
- `transport.py` — shared network policy

## Minimum implementation for a new board

Subclass `RequestResponseConnector` and implement two methods:

```python
class ExampleConnector(RequestResponseConnector):
    source = "example"

    def build_request(self, query, cursor):
        return RequestSpec(
            method="GET",
            url="https://api.example.com/jobs",
            kwargs={"params": {"q": query.keyword_text, "page": cursor or "1"}},
        )

    def parse_response(self, response, query, cursor):
        payload = response.json()
        jobs = [normalize(item) for item in payload["results"]]
        return SearchPage(jobs=jobs, next_cursor=payload.get("next_page"))
```

The `normalize` function must return `NormalizedJob` and should map at least:

- `source` and a stable `source_job_id`
- `title` and `company`
- canonical `job_url` (without tracking parameters where possible)
- location, posted date, description, salary, and apply URL when supplied
- original provider fields in `raw` when useful for debugging

## Registration checklist

1. Add the connector and parser under `src/job_agent/`.
2. Export the connector from `connectors/__init__.py`.
3. Instantiate it in `registry.py` only when its environment configuration is present.
4. Add its public route name and internal source prefix to `PROVIDER_ROUTES` in `provider_routes.py`.
5. Document its environment variables in `.env.example`.
6. Add fixture-based parser tests and mocked HTTP/pagination tests.
7. Run `python -m pytest` before a live smoke test.

Keep provider credentials and parameter names inside that provider's adapter. Do not add board-specific fields to the API response; map them into `NormalizedJob`. A provider that requires login, CAPTCHA solving, or another access-control bypass should stop with a clear connector error instead of attempting a bypass.
