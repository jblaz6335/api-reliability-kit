import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from api_reliability import (
    ApiAutomationEngine,
    AuthenticationError,
    ConflictError,
    HttpResponse,
    PaginationError,
    ResponseError,
    RetryExhausted,
    SourceSpec,
    ValidationError,
)


def scripted(*responses):
    queue = list(responses)
    requests = []

    def transport(request):
        requests.append(request)
        return queue.pop(0)

    transport.requests = requests
    return transport


def spec(**overrides):
    values = {"name": "demo", "url": "https://api.example.invalid/items"}
    values.update(overrides)
    return SourceSpec(**values)


class ApiAutomationEngineTests(unittest.TestCase):
    def test_accepts_one_page(self):
        transport = scripted(HttpResponse(200, {"data": [{"id": 1, "name": "Ada"}]}))
        result = ApiAutomationEngine(transport).run(spec())
        self.assertEqual(result.records, [{"id": 1, "name": "Ada"}])

    def test_field_mapping_normalizes_nested_values(self):
        transport = scripted(HttpResponse(200, {"data": [{"id": "A", "profile": {"name": "Ada"}}]}))
        result = ApiAutomationEngine(transport).run(spec(field_map={"record_id": "id", "name": "profile.name"}))
        self.assertEqual(result.records, [{"record_id": "A", "name": "Ada"}])

    def test_exact_retry_is_idempotent_across_runs(self):
        response = HttpResponse(200, {"data": [{"id": "A", "name": "Ada"}]})
        first = ApiAutomationEngine(scripted(response)).run(spec())
        second = ApiAutomationEngine(scripted(response)).run(spec(), first.state)
        self.assertEqual(second.records, [])
        self.assertEqual(second.audit[-1]["duplicates"], 1)

    def test_conflicting_identifier_is_rejected(self):
        first = ApiAutomationEngine(scripted(HttpResponse(200, {"data": [{"id": "A", "name": "Ada"}]}))).run(spec())
        with self.assertRaises(ConflictError):
            ApiAutomationEngine(scripted(HttpResponse(200, {"data": [{"id": "A", "name": "Grace"}]}))).run(spec(), first.state)

    def test_rate_limit_is_retried(self):
        transport = scripted(HttpResponse(429, {}), HttpResponse(200, {"data": []}))
        result = ApiAutomationEngine(transport).run(spec(max_retries=1))
        self.assertEqual(result.audit[0], {"event": "retry", "status": 429, "attempt": 1})

    def test_server_error_is_retried(self):
        transport = scripted(HttpResponse(503, {}), HttpResponse(200, {"data": []}))
        ApiAutomationEngine(transport).run(spec(max_retries=1))
        self.assertEqual(len(transport.requests), 2)

    def test_retry_exhaustion_fails_closed(self):
        with self.assertRaises(RetryExhausted):
            ApiAutomationEngine(scripted(HttpResponse(500, {}), HttpResponse(500, {}))).run(spec(max_retries=1))

    def test_authentication_error_is_not_retried(self):
        transport = scripted(HttpResponse(401, {}))
        with self.assertRaises(AuthenticationError):
            ApiAutomationEngine(transport).run(spec(max_retries=3))
        self.assertEqual(len(transport.requests), 1)

    def test_non_retryable_error_fails(self):
        with self.assertRaises(ResponseError):
            ApiAutomationEngine(scripted(HttpResponse(422, {}))).run(spec())

    def test_malformed_json_fails(self):
        with self.assertRaises(ResponseError):
            ApiAutomationEngine(scripted(HttpResponse(200, "{broken"))).run(spec())

    def test_missing_data_path_fails(self):
        with self.assertRaises(ResponseError):
            ApiAutomationEngine(scripted(HttpResponse(200, {"results": []}))).run(spec())

    def test_page_pagination_increments_and_stops(self):
        transport = scripted(
            HttpResponse(200, {"data": [{"id": 1}], "has_more": True}),
            HttpResponse(200, {"data": [{"id": 2}], "has_more": False}),
        )
        result = ApiAutomationEngine(transport).run(spec(pagination="page"))
        self.assertEqual([r["params"]["page"] for r in transport.requests], [1, 2])
        self.assertEqual(len(result.records), 2)

    def test_cursor_pagination_passes_next_cursor(self):
        transport = scripted(
            HttpResponse(200, {"data": [{"id": 1}], "next_cursor": "c2"}),
            HttpResponse(200, {"data": [{"id": 2}], "next_cursor": None}),
        )
        ApiAutomationEngine(transport).run(spec(pagination="cursor"))
        self.assertEqual(transport.requests[1]["params"], {"cursor": "c2"})

    def test_repeated_cursor_fails_closed(self):
        transport = scripted(
            HttpResponse(200, {"data": [{"id": 1}], "next_cursor": "c2"}),
            HttpResponse(200, {"data": [{"id": 2}], "next_cursor": "c2"}),
        )
        with self.assertRaises(PaginationError):
            ApiAutomationEngine(transport).run(spec(pagination="cursor"))

    def test_max_pages_prevents_infinite_page_loop(self):
        transport = scripted(HttpResponse(200, {"data": [{"id": 1}], "has_more": True}))
        with self.assertRaises(PaginationError):
            ApiAutomationEngine(transport).run(spec(pagination="page", max_pages=1))

    def test_http_url_is_rejected(self):
        with self.assertRaises(ValidationError):
            ApiAutomationEngine(scripted()).run(spec(url="http://example.invalid/items"))

    def test_request_contains_env_name_not_secret_value(self):
        transport = scripted(HttpResponse(200, {"data": []}))
        ApiAutomationEngine(transport).run(spec(auth_env="BUYER_API_TOKEN"))
        serialized = json.dumps(transport.requests)
        self.assertIn("BUYER_API_TOKEN", serialized)
        self.assertNotIn("Bearer ", serialized)

    def test_state_is_json_round_trip_safe(self):
        result = ApiAutomationEngine(scripted(HttpResponse(200, {"data": [{"id": "A"}]}))).run(spec())
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(json.dumps(result.state), encoding="utf-8")
            loaded = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(loaded, result.state)


if __name__ == "__main__":
    unittest.main()
