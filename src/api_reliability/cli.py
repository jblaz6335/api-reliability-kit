import json

from .engine import ApiAutomationEngine, HttpResponse, SourceSpec


def main() -> None:
    responses = iter([
        HttpResponse(429, {"error": "synthetic rate limit"}),
        HttpResponse(200, {"data": [{"id": "A-1", "profile": {"name": "Ada"}}, {"id": "A-2", "profile": {"name": "Grace"}}], "next_cursor": "page-2"}),
        HttpResponse(200, {"data": [{"id": "A-2", "profile": {"name": "Grace"}}, {"id": "A-3", "profile": {"name": "Katherine"}}], "next_cursor": None}),
    ])
    engine = ApiAutomationEngine(lambda request: next(responses))
    result = engine.run(SourceSpec(
        name="synthetic_contacts",
        url="https://api.example.invalid/contacts",
        pagination="cursor",
        field_map={"contact_id": "id", "name": "profile.name"},
        max_retries=2,
        auth_env="DEMO_API_TOKEN",
    ))
    print(json.dumps(result.snapshot(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
