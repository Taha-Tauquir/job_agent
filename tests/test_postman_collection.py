import json
from pathlib import Path

from fastapi.routing import APIRoute

from job_agent.api import app


COLLECTION_PATH = (
    Path(__file__).parents[1]
    / "postman"
    / "Personal Job Agent.postman_collection.json"
)


def _collection_requests(items: list[dict]) -> set[tuple[str, str]]:
    requests: set[tuple[str, str]] = set()
    for item in items:
        requests.update(_collection_requests(item.get("item", [])))
        request = item.get("request")
        if not request:
            continue
        raw_url = request["url"] if isinstance(request["url"], str) else request["url"]["raw"]
        path = raw_url.removeprefix("{{baseUrl}}")
        path = path.replace("{{scheduleId}}", "{schedule_id}")
        requests.add((request["method"].upper(), path))
    return requests


def test_postman_collection_is_valid_and_covers_every_api_route() -> None:
    collection = json.loads(COLLECTION_PATH.read_text(encoding="utf-8"))
    documented = _collection_requests(collection["item"])
    application_routes = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api/")
        for method in route.methods
    }
    assert application_routes == documented
