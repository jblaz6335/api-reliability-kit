from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from typing import Any, Callable
from urllib.parse import urlparse


class ApiError(Exception):
    """Base error for a bounded API run."""


class ValidationError(ApiError):
    pass


class AuthenticationError(ApiError):
    pass


class ResponseError(ApiError):
    pass


class RetryExhausted(ApiError):
    pass


class ConflictError(ApiError):
    pass


class PaginationError(ApiError):
    pass


def _path_get(value: Any, path: str, *, missing: Any = None) -> Any:
    current = value
    if not path:
        return current
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return missing
        current = current[part]
    return current


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(value: Any) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: Any
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceSpec:
    name: str
    url: str
    data_path: str = "data"
    id_field: str = "id"
    field_map: dict[str, str] = field(default_factory=dict)
    pagination: str = "none"
    page_param: str = "page"
    cursor_param: str = "cursor"
    next_cursor_path: str = "next_cursor"
    has_more_path: str = "has_more"
    start_page: int = 1
    max_pages: int = 25
    max_retries: int = 2
    timeout_seconds: int = 20
    auth_env: str | None = None

    def validate(self) -> None:
        if not self.name.strip():
            raise ValidationError("source name is required")
        parsed = urlparse(self.url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValidationError("source URL must be an absolute HTTPS URL")
        if self.pagination not in {"none", "page", "cursor"}:
            raise ValidationError("pagination must be none, page, or cursor")
        if self.max_pages < 1 or self.max_pages > 1000:
            raise ValidationError("max_pages must be between 1 and 1000")
        if self.max_retries < 0 or self.max_retries > 10:
            raise ValidationError("max_retries must be between 0 and 10")
        if self.timeout_seconds < 1 or self.timeout_seconds > 300:
            raise ValidationError("timeout_seconds must be between 1 and 300")
        if self.auth_env is not None and not self.auth_env.replace("_", "").isalnum():
            raise ValidationError("auth_env must be an environment-variable name, not a secret")


@dataclass
class RunResult:
    records: list[dict[str, Any]]
    audit: list[dict[str, Any]]
    state: dict[str, Any]

    def snapshot(self) -> dict[str, Any]:
        return {"records": self.records, "audit": self.audit, "state": self.state}


class ApiAutomationEngine:
    """Runs one source through a deterministic, provider-neutral reliability boundary."""

    def __init__(self, transport: Callable[[dict[str, Any]], HttpResponse]):
        self.transport = transport

    def run(self, spec: SourceSpec, state: dict[str, Any] | None = None) -> RunResult:
        spec.validate()
        stored = json.loads(json.dumps(state or {}))
        seen: dict[str, str] = dict(stored.get("seen", {}))
        cursor = stored.get("cursor")
        page = int(stored.get("page", spec.start_page))
        records: list[dict[str, Any]] = []
        audit: list[dict[str, Any]] = []
        pages_completed = 0
        used_cursors: set[str] = set()

        while pages_completed < spec.max_pages:
            params: dict[str, Any] = {}
            if spec.pagination == "page":
                params[spec.page_param] = page
            elif spec.pagination == "cursor" and cursor is not None:
                params[spec.cursor_param] = cursor

            request = {
                "source": spec.name,
                "url": spec.url,
                "method": "GET",
                "params": params,
                "timeout_seconds": spec.timeout_seconds,
                "auth_env": spec.auth_env,
            }
            response = self._request_with_retries(request, spec, audit)
            payload = self._decode(response.body)
            raw_records = _path_get(payload, spec.data_path, missing=object())
            if not isinstance(raw_records, list):
                raise ResponseError(f"{spec.data_path!r} must resolve to a list")

            accepted = 0
            duplicates = 0
            for raw in raw_records:
                if not isinstance(raw, dict):
                    raise ResponseError("every data item must be an object")
                normalized = self._normalize(raw, spec.field_map)
                identifier = _path_get(raw, spec.id_field, missing=None)
                if identifier is None or str(identifier).strip() == "":
                    raise ResponseError(f"record is missing stable id field {spec.id_field!r}")
                key = str(identifier)
                digest = _fingerprint(normalized)
                if key in seen:
                    if seen[key] != digest:
                        raise ConflictError(f"record id {key!r} was reused with different content")
                    duplicates += 1
                    continue
                seen[key] = digest
                records.append(normalized)
                accepted += 1

            pages_completed += 1
            audit.append({
                "event": "page_processed",
                "page_number": pages_completed,
                "accepted": accepted,
                "duplicates": duplicates,
                "status": response.status,
            })

            if spec.pagination == "none":
                break
            if spec.pagination == "page":
                has_more = _path_get(payload, spec.has_more_path, missing=False)
                if has_more is not True:
                    break
                page += 1
                continue

            next_cursor = _path_get(payload, spec.next_cursor_path, missing=None)
            if next_cursor in {None, ""}:
                break
            next_cursor = str(next_cursor)
            if next_cursor == cursor or next_cursor in used_cursors:
                raise PaginationError("cursor repeated; refusing an infinite loop")
            used_cursors.add(next_cursor)
            cursor = next_cursor
        else:
            raise PaginationError("max_pages reached while the source still indicated more data")

        return RunResult(
            records=records,
            audit=audit,
            state={"seen": seen, "page": page, "cursor": cursor, "pages_completed": pages_completed},
        )

    def _request_with_retries(
        self,
        request: dict[str, Any],
        spec: SourceSpec,
        audit: list[dict[str, Any]],
    ) -> HttpResponse:
        for attempt in range(spec.max_retries + 1):
            response = self.transport(json.loads(json.dumps(request)))
            if not isinstance(response, HttpResponse):
                raise ResponseError("transport must return HttpResponse")
            if 200 <= response.status < 300:
                return response
            if response.status in {401, 403}:
                raise AuthenticationError(f"authentication failed with HTTP {response.status}")
            if response.status == 429 or 500 <= response.status <= 599:
                audit.append({"event": "retry", "status": response.status, "attempt": attempt + 1})
                if attempt < spec.max_retries:
                    continue
                raise RetryExhausted(f"HTTP {response.status} exceeded {spec.max_retries} retries")
            raise ResponseError(f"non-retryable HTTP {response.status}")
        raise AssertionError("unreachable retry state")

    @staticmethod
    def _decode(body: Any) -> dict[str, Any]:
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as exc:
                raise ResponseError("response body is not valid JSON") from exc
        if not isinstance(body, dict):
            raise ResponseError("response JSON must be an object")
        return body

    @staticmethod
    def _normalize(raw: dict[str, Any], field_map: dict[str, str]) -> dict[str, Any]:
        if not field_map:
            return json.loads(json.dumps(raw, sort_keys=True))
        normalized: dict[str, Any] = {}
        missing = object()
        for target, source_path in field_map.items():
            value = _path_get(raw, source_path, missing=missing)
            if value is missing:
                raise ResponseError(f"mapped field {source_path!r} is missing")
            normalized[target] = value
        return normalized
