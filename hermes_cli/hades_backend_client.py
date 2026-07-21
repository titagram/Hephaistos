"""HTTP client and small contract helpers for the Hades Laravel backend."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from typing import Any, BinaryIO, Iterator, Mapping
from urllib.parse import unquote

import httpx

from hermes_cli.hades_graph_v2 import (
    GraphContractError,
    canonical_json_bytes,
    validate_schema,
)
from hermes_cli.hades_graph_v2.bundle import (
    MAX_MANIFEST_BYTES as _MAX_GRAPH_MANIFEST_BYTES,
)


API_PREFIX = "/api/hades/v1"
# The envelope permits a 64 KiB payload.  SSE adds bounded metadata around it,
# but no single line or unfinished event block may grow without limit.
PERSEPHONE_SSE_MAX_EVENT_BYTES = 65_536 + 16_384
PERSEPHONE_SSE_MAX_LINE_BYTES = PERSEPHONE_SSE_MAX_EVENT_BYTES
PERSEPHONE_SSE_MAX_FIELD_BYTES = 4_096
_SECRET_PATTERNS = (
    re.compile(
        r"(?i)hades_(?:agent|bootstrap)_[0-9A-HJKMNP-TV-Z]{26}\|[A-Za-z0-9]{64}"
    ),
    re.compile(r"sk-[A-Za-z0-9][A-Za-z0-9_\-]{6,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)(token[=:]\s*)[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)(api[_-]?key[=:]\s*)[A-Za-z0-9._\-]{8,}"),
)
_WIKI_PAGE_ID = re.compile(r"\A[0-9A-HJKMNP-TV-Z]{26}\Z")
_LOGBOOK_ROUTE_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_-]{0,191}\Z")
_ROUTE_CONTROL_CHARACTER = re.compile(r"[\x00-\x1F\x7F]")
_SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")
_GRAPH_IMPORT_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_ERROR_CODE = re.compile(r"\A[a-z][a-z0-9_]{0,127}\Z")
_RFC3339_DATE_TIME = re.compile(
    r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\Z"
)
_MAX_GRAPH_CHUNK_INDEX = 511
_MAX_GRAPH_CHUNK_BYTES = 8 * 1024 * 1024
_GRAPH_CREATE_ERROR_CODES = {
    404: frozenset({"graph_import_not_found"}),
    409: frozenset({"graph_import_manifest_conflict"}),
    422: frozenset({"graph_manifest_invalid"}),
}
_GRAPH_CHUNK_ERROR_CODES = {
    404: frozenset({"graph_import_not_found"}),
    409: frozenset({"chunk_digest_conflict"}),
    422: frozenset({
        "graph_chunk_invalid",
        "graph_chunk_too_large",
        "graph_import_not_staging",
    }),
}
_GRAPH_COMPLETE_ERROR_CODES = {
    404: frozenset({"graph_import_not_found"}),
    409: frozenset({"graph_import_failed"}),
    410: frozenset({"graph_import_stale"}),
    422: frozenset({"graph_manifest_invalid", "graph_import_incomplete"}),
}
_GRAPH_GET_ERROR_CODES = {404: frozenset({"graph_import_not_found"})}


def validate_graph_import_id(value: Any) -> str:
    """Return one opaque graph-import route segment or reject it locally."""

    clean = str(value or "").strip()
    if _GRAPH_IMPORT_ID.fullmatch(clean) is None:
        raise ValueError("graph import id must be a safe route segment")
    return clean


def _require_rfc3339_date_time(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or _RFC3339_DATE_TIME.fullmatch(value) is None:
        raise HadesBackendError(
            f"graph import response {field} must be RFC3339 date-time"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HadesBackendError(
            f"graph import response {field} must be RFC3339 date-time"
        ) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HadesBackendError(
            f"graph import response {field} must be RFC3339 date-time"
        )
    return value


def _safe_error_code(value: Any) -> str | None:
    return value if isinstance(value, str) and _ERROR_CODE.fullmatch(value) else None


@dataclass(frozen=True, slots=True)
class ChunkHeaders:
    """Digest and size descriptor for one immutable graph-v2 chunk."""

    sha256: str
    uncompressed_bytes: int
    compressed_sha256: str
    compressed_bytes: int

    def __post_init__(self) -> None:
        if not _SHA256.fullmatch(self.sha256):
            raise ValueError("chunk sha256 must be a lower-case SHA-256 digest")
        if not _SHA256.fullmatch(self.compressed_sha256):
            raise ValueError(
                "chunk compressed_sha256 must be a lower-case SHA-256 digest"
            )
        if type(self.uncompressed_bytes) is not int or self.uncompressed_bytes < 1:
            raise ValueError("chunk uncompressed_bytes must be a positive integer")
        if self.uncompressed_bytes > _MAX_GRAPH_CHUNK_BYTES:
            raise ValueError("chunk uncompressed_bytes must be at most 8 MiB")
        if type(self.compressed_bytes) is not int or self.compressed_bytes < 1:
            raise ValueError("chunk compressed_bytes must be a positive integer")
        if self.compressed_bytes > _MAX_GRAPH_CHUNK_BYTES:
            raise ValueError("chunk compressed_bytes must be at most 8 MiB")

    def as_http_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/vnd.hades.graph-chunk+gzip",
            "X-Hades-Chunk-Sha256": self.sha256,
            "X-Hades-Chunk-Uncompressed-Bytes": str(self.uncompressed_bytes),
            "X-Hades-Chunk-Compressed-Sha256": self.compressed_sha256,
            "X-Hades-Chunk-Compressed-Bytes": str(self.compressed_bytes),
        }


@dataclass(frozen=True, slots=True)
class GraphChunkState:
    index: int
    status: str

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "GraphChunkState":
        unexpected = set(payload) - {"index", "status"}
        if unexpected:
            raise HadesBackendError("graph chunk response has unexpected fields")
        index = payload.get("index")
        status = payload.get("status")
        if (
            type(index) is not int
            or not 0 <= index <= _MAX_GRAPH_CHUNK_INDEX
            or status != "accepted"
        ):
            raise HadesBackendError("graph chunk response has an invalid contract")
        return cls(index=index, status=status)


@dataclass(frozen=True, slots=True)
class GraphImportState:
    import_id: str
    attempt_generation: int | None
    validation_status: str
    publication_status: str
    missing_chunk_indexes: tuple[int, ...]
    expires_at: str | None
    received_chunks: int | None
    expected_chunks: int | None
    failure: dict[str, Any] | None
    projection_version: str | None

    @property
    def is_ready(self) -> bool:
        return (
            self.validation_status == "validated"
            and self.publication_status == "ready"
            and self.projection_version is not None
        )

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "GraphImportState":
        import_id = payload.get("import_id")
        validation = payload.get("validation_status")
        publication = payload.get("publication_status")
        try:
            import_id = validate_graph_import_id(import_id)
        except ValueError:
            raise HadesBackendError(
                "graph import response has an invalid import_id"
            ) from None
        if validation not in {"staging", "validating", "validated", "failed", "stale"}:
            raise HadesBackendError(
                "graph import response has an invalid validation status"
            )
        if publication not in {
            "not_requested",
            "queued",
            "projecting",
            "ready",
            "failed",
            "stale",
        }:
            raise HadesBackendError(
                "graph import response has an invalid publication status"
            )
        raw_missing = payload.get("missing_chunk_indexes", [])
        if (
            not isinstance(raw_missing, list)
            or any(
                type(index) is not int or not 0 <= index <= _MAX_GRAPH_CHUNK_INDEX
                for index in raw_missing
            )
            or raw_missing != sorted(set(raw_missing))
        ):
            raise HadesBackendError("graph import response has invalid missing chunks")

        attempt = payload.get("attempt_generation")
        received = payload.get("received_chunks")
        expected = payload.get("expected_chunks")
        for name, value, positive in (
            ("attempt_generation", attempt, True),
            ("received_chunks", received, False),
            ("expected_chunks", expected, False),
        ):
            if value is not None and (
                type(value) is not int or value < (1 if positive else 0)
            ):
                raise HadesBackendError(f"graph import response has invalid {name}")
        projection = payload.get("projection_version")
        if projection is not None and not (
            isinstance(projection, str) and _SHA256.fullmatch(projection)
        ):
            raise HadesBackendError(
                "graph import response has invalid projection_version"
            )
        expires_at = payload.get("expires_at")
        if expires_at is not None:
            expires_at = _require_rfc3339_date_time(expires_at, field="expires_at")
        failure = payload.get("failure")
        if failure is not None and not isinstance(failure, dict):
            raise HadesBackendError("graph import response has invalid failure")
        if failure is not None:
            if set(failure) != {"code", "details"}:
                raise HadesBackendError(
                    "graph import response failure has unexpected fields"
                )
            failure_code = _safe_error_code(failure.get("code"))
            if failure_code is None:
                raise HadesBackendError(
                    "graph import response has invalid failure code"
                )
            failure_details = failure.get("details")
            if failure_details is not None and not isinstance(failure_details, dict):
                raise HadesBackendError(
                    "graph import response has invalid failure details"
                )
            failure = {
                "code": failure_code,
                "details": _redact_structured(failure_details),
            }
        return cls(
            import_id=import_id,
            attempt_generation=attempt,
            validation_status=validation,
            publication_status=publication,
            missing_chunk_indexes=tuple(raw_missing),
            expires_at=expires_at,
            received_chunks=received,
            expected_chunks=expected,
            failure=failure,
            projection_version=projection,
        )


class HadesBackendError(RuntimeError):
    """Raised when the Hades backend returns an error response."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        next_step: str | None = None,
        details: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.next_step = next_step
        self.details = details


@dataclass(frozen=True, slots=True)
class _BackendResponse:
    status_code: int
    payload: dict[str, Any] | list[Any]


def _parse_graph_import_response(
    payload: Mapping[str, Any],
    *,
    operation: str,
    http_status: int,
    response_fields: frozenset[str],
    requested_import_id: str | None = None,
    expected_chunks: int | None = None,
) -> GraphImportState:
    missing = sorted(response_fields - set(payload))
    if missing:
        raise HadesBackendError(
            f"graph import {operation} response is missing {', '.join(missing)}"
        )
    unexpected = sorted(set(payload) - response_fields)
    if unexpected:
        raise HadesBackendError(
            f"graph import {operation} response has unexpected fields: "
            f"{', '.join(unexpected)}"
        )
    state = GraphImportState.from_mapping(payload)
    if requested_import_id is not None and state.import_id != requested_import_id:
        raise HadesBackendError(
            f"graph import {operation} response import_id does not match request"
        )
    if operation == "create":
        if state.attempt_generation is None or expected_chunks is None:
            raise HadesBackendError(
                "graph import create response has invalid attempt_generation"
            )
        if any(index >= expected_chunks for index in state.missing_chunk_indexes):
            raise HadesBackendError(
                "graph import create response has invalid missing chunk indexes"
            )
        new_attempt = (
            state.validation_status == "staging"
            and state.publication_status == "not_requested"
            and state.expires_at is not None
        )
        replay = state.validation_status in {"staging", "validating", "validated"}
        if state.validation_status == "staging":
            replay = replay and state.publication_status == "not_requested"
            replay = replay and state.expires_at is not None
        elif state.validation_status == "validating":
            replay = replay and state.publication_status == "not_requested"
            replay = replay and state.expires_at is None
            replay = replay and not state.missing_chunk_indexes
        elif state.validation_status == "validated":
            replay = replay and state.expires_at is None
            replay = replay and not state.missing_chunk_indexes
        if (http_status == 201 and not new_attempt) or (
            http_status == 200 and not replay
        ):
            raise HadesBackendError(
                f"graph import create response has an illegal state for HTTP {http_status}"
            )
    elif operation == "complete":
        ready = (
            state.validation_status == "validated"
            and state.publication_status == "ready"
            and state.projection_version is not None
        )
        pending = (
            state.validation_status == "validating"
            and state.publication_status == "not_requested"
            and state.projection_version is None
        ) or (
            state.validation_status == "validated"
            and state.publication_status != "ready"
            and state.projection_version is None
        )
        if (http_status == 200 and not ready) or (http_status == 202 and not pending):
            raise HadesBackendError(
                f"graph import complete response has an illegal state for HTTP {http_status}"
            )
    elif operation == "get":
        received = state.received_chunks
        expected = state.expected_chunks
        if received is None or expected is None:
            raise HadesBackendError(
                "graph import get response has invalid received_chunks or expected_chunks"
            )
        if (
            not 0 <= received <= expected <= _MAX_GRAPH_CHUNK_INDEX + 1
            or len(state.missing_chunk_indexes) != expected - received
            or any(index >= expected for index in state.missing_chunk_indexes)
        ):
            raise HadesBackendError(
                "graph import get response has invalid chunk accounting"
            )
        all_received = received == expected and not state.missing_chunk_indexes
        legal = False
        if state.validation_status == "staging":
            legal = (
                state.publication_status == "not_requested"
                and state.expires_at is not None
                and state.failure is None
                and state.projection_version is None
            )
        elif state.validation_status == "validating":
            legal = (
                state.publication_status == "not_requested"
                and state.expires_at is None
                and state.projection_version is None
                and all_received
            )
        elif state.validation_status == "validated":
            legal = state.expires_at is None and all_received
            if state.publication_status == "ready":
                legal = legal and state.projection_version is not None
                legal = legal and state.failure is None
            elif state.publication_status == "failed":
                legal = legal and state.failure is not None
            else:
                legal = legal and state.failure is None
        elif state.validation_status == "failed":
            legal = (
                state.publication_status == "not_requested"
                and state.expires_at is None
                and state.failure is not None
                and state.projection_version is None
                and all_received
            )
        elif state.validation_status == "stale":
            legal = (
                state.publication_status == "not_requested"
                and state.expires_at is None
                and state.projection_version is None
            )
        if not legal:
            raise HadesBackendError("graph import get response has an illegal state")
    return state


def _normalize_base_url(base_url: str) -> str:
    value = str(base_url or "").strip().rstrip("/")
    if not value:
        raise ValueError("backend base URL is required")
    return value


def token_env_key(base_url: str, project_id: str, agent_id: str) -> str:
    """Return the profile-secret env key for a derived backend agent token."""
    material = "|".join((
        _normalize_base_url(base_url).lower(),
        str(project_id or "").strip(),
        str(agent_id or "").strip(),
    ))
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16].upper()
    return f"HADES_BACKEND_AGENT_TOKEN_{digest}"


def plugin_token_env_key(base_url: str, project_id: str, agent_id: str) -> str:
    """Return the profile-secret env key for a bootstrap-derived Plugin token."""
    return token_env_key(base_url, project_id, agent_id).replace(
        "AGENT_TOKEN", "PLUGIN_TOKEN"
    )


def plugin_device_secret_env_key(base_url: str, project_id: str, agent_id: str) -> str:
    """Return the profile-secret env key for the Plugin request-signing secret."""
    return token_env_key(base_url, project_id, agent_id).replace(
        "AGENT_TOKEN", "PLUGIN_DEVICE_SECRET"
    )


def redact_secret(text: Any) -> str:
    """Redact likely backend/API secrets from text before surfacing errors."""
    value = (
        text if isinstance(text, str) else json.dumps(text, sort_keys=True, default=str)
    )
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub(
            lambda m: (m.group(1) if m.lastindex else "") + "***", value
        )
    return value


def _redact_structured(value: Any) -> Any:
    """Redact secrets recursively while preserving backend error structure."""

    if isinstance(value, str):
        return redact_secret(value)
    if isinstance(value, dict):
        return {
            redact_secret(key) if isinstance(key, str) else key: _redact_structured(
                item
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_structured(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_structured(item) for item in value)
    return value


def validate_wiki_page_id(value: Any) -> str:
    """Return a canonical opaque wiki page ULID or reject it before routing."""
    clean = str(value or "").strip()
    if _WIKI_PAGE_ID.fullmatch(clean) is None:
        raise ValueError("wiki page id must be a canonical ULID")
    return clean


def validate_logbook_route_id(value: Any, *, field: str) -> str:
    """Return one safe project-scoped logbook route identifier."""

    clean = str(value or "").strip()
    if _LOGBOOK_ROUTE_ID.fullmatch(clean) is None:
        raise ValueError(f"{field} must be a safe opaque route identifier")
    return clean


def _string_param(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (str, int, float)):
        return str(value)
    return json.dumps(value, sort_keys=True, default=str)


def _query_params(payload: dict[str, Any] | None) -> list[tuple[str, str]] | None:
    if not payload:
        return None
    params: list[tuple[str, str]] = []
    for key, value in payload.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            params.extend((f"{key}[]", _string_param(item)) for item in value)
        else:
            params.append((key, _string_param(value)))
    return params


def _iter_bounded_sse_lines(response: httpx.Response) -> Iterator[str]:
    """Split SSE lines without allowing an unterminated line to grow forever."""
    pending = bytearray()
    swallow_lf = False
    for chunk in response.iter_bytes():
        offset = 0
        if swallow_lf:
            if chunk.startswith(b"\n"):
                offset = 1
            swallow_lf = False
        while offset < len(chunk):
            lf = chunk.find(b"\n", offset)
            cr = chunk.find(b"\r", offset)
            separators = [position for position in (lf, cr) if position >= 0]
            if not separators:
                remaining = memoryview(chunk)[offset:]
                if len(pending) + len(remaining) > PERSEPHONE_SSE_MAX_LINE_BYTES:
                    raise HadesBackendError(
                        "Persephone stream line exceeds the size limit",
                        code="stream_malformed",
                    )
                pending.extend(remaining)
                break
            separator = min(separators)
            segment = memoryview(chunk)[offset:separator]
            if len(pending) + len(segment) > PERSEPHONE_SSE_MAX_LINE_BYTES:
                raise HadesBackendError(
                    "Persephone stream line exceeds the size limit",
                    code="stream_malformed",
                )
            pending.extend(segment)
            try:
                yield pending.decode("utf-8")
            except UnicodeDecodeError:
                raise HadesBackendError(
                    "Persephone stream contains invalid UTF-8",
                    code="stream_malformed",
                ) from None
            pending.clear()
            if chunk[separator] == 13:  # CR optionally consumes one following LF.
                if separator + 1 < len(chunk) and chunk[separator + 1] == 10:
                    offset = separator + 2
                else:
                    offset = separator + 1
                    swallow_lf = offset == len(chunk)
            else:
                offset = separator + 1
    if pending:
        try:
            yield pending.decode("utf-8")
        except UnicodeDecodeError:
            raise HadesBackendError(
                "Persephone stream contains invalid UTF-8",
                code="stream_malformed",
            ) from None


class HadesBackendClient:
    """Small synchronous client for the Laravel Hades API."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = _normalize_base_url(base_url)
        self.token = str(token or "").strip()
        if not self.token:
            raise ValueError("backend token is required")
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                "User-Agent": "hades-agent/backend-client",
            },
        )

    def close(self) -> None:
        self._client.close()

    def _url(self, path: str) -> str:
        clean = str(path or "").strip().strip("/")
        decoded = clean
        for _ in range(2):
            decoded_once = unquote(decoded)
            if decoded_once == decoded:
                break
            decoded = decoded_once
        segments = decoded.split("/")
        if (
            not clean
            or any(marker in decoded for marker in ("?", "#", "\\"))
            or _ROUTE_CONTROL_CHARACTER.search(decoded)
            or any(not segment or segment in {".", ".."} for segment in segments)
        ):
            raise ValueError("backend route path must be a safe relative path")
        return f"{API_PREFIX}/{clean}"

    def _request_with_status(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        content_body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        success_statuses: tuple[int, ...] | None = None,
        error_codes_by_status: Mapping[int, frozenset[str]] | None = None,
    ) -> _BackendResponse:
        if json_body is not None and content_body is not None:
            raise ValueError("a backend request cannot have both JSON and raw content")
        try:
            response = self._client.request(
                method,
                self._url(path),
                json=json_body,
                content=content_body,
                params=_query_params(params),
                headers=dict(headers or {}),
            )
        except httpx.HTTPError as exc:
            raise HadesBackendError(redact_secret(str(exc))) from exc
        if response.status_code >= 400:
            try:
                body: Any = response.json()
            except ValueError:
                body = response.text
            code: str | None = None
            next_step = None
            details = None
            if (
                error_codes_by_status is not None
                and response.status_code in error_codes_by_status
            ):
                error = body.get("error") if isinstance(body, dict) else None
                allowed_error_fields = {"code", "message", "next_step", "details"}
                contract_is_valid = (
                    isinstance(body, dict)
                    and set(body) == {"error"}
                    and isinstance(error, dict)
                    and {"code", "message"}.issubset(error)
                    and not set(error) - allowed_error_fields
                    and _safe_error_code(error.get("code"))
                    in error_codes_by_status[response.status_code]
                    and isinstance(error.get("message"), str)
                    and (
                        error.get("next_step") is None
                        or isinstance(error.get("next_step"), str)
                    )
                    and (
                        error.get("details") is None
                        or isinstance(error.get("details"), dict)
                    )
                )
                if not contract_is_valid:
                    raise HadesBackendError(
                        f"{response.status_code}: backend returned an invalid graph error response",
                        status_code=response.status_code,
                    )
            if isinstance(body, dict):
                error = body.get("error")
                if isinstance(error, dict):
                    code = _safe_error_code(error.get("code"))
                    raw_next_step = error.get("next_step")
                    next_step = (
                        redact_secret(raw_next_step)
                        if isinstance(raw_next_step, str) and raw_next_step
                        else None
                    )
                    details = _redact_structured(error.get("details"))
            raise HadesBackendError(
                f"{response.status_code}: {redact_secret(body)}",
                status_code=response.status_code,
                code=code,
                next_step=next_step,
                details=details,
            )
        if (
            success_statuses is not None
            and response.status_code not in success_statuses
        ):
            raise HadesBackendError(
                f"backend returned unexpected success status {response.status_code}",
                status_code=response.status_code,
            )
        if not response.content:
            return _BackendResponse(response.status_code, {})
        try:
            data = response.json()
        except ValueError as exc:
            raise HadesBackendError(
                f"invalid JSON response from backend: {redact_secret(response.text)}"
            ) from exc
        if not isinstance(data, (dict, list)):
            raise HadesBackendError("backend response must be a JSON object or array")
        return _BackendResponse(response.status_code, data)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        content_body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        success_statuses: tuple[int, ...] | None = None,
    ) -> dict[str, Any] | list[Any]:
        return self._request_with_status(
            method,
            path,
            json_body=json_body,
            params=params,
            content_body=content_body,
            headers=headers,
            success_statuses=success_statuses,
        ).payload

    def health(self) -> dict[str, Any]:
        return self._request("GET", "health")

    def capabilities(self) -> dict[str, Any]:
        return self._request("GET", "capabilities")

    def verify_token(self, *, project_id: str) -> dict[str, Any]:
        return self._request(
            "POST", "token/verify", json_body={"project_id": project_id}
        )

    def register_agent(
        self,
        *,
        project_id: str,
        agent_id: str,
        label: str,
        platform: str,
        version: str,
        capabilities: list[str],
        plugin_device: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "agents/register",
            json_body={
                "project_id": project_id,
                "agent_id": agent_id,
                "label": label,
                "platform": platform,
                "version": version,
                "capabilities": capabilities,
                **({"plugin_device": plugin_device} if plugin_device else {}),
            },
        )

    def bind_workspace(self, **payload: Any) -> dict[str, Any]:
        return self._request("POST", "workspaces/bind", json_body=payload)

    def unlink_workspace(
        self, workspace_binding_id: str, **payload: Any
    ) -> dict[str, Any]:
        clean = str(workspace_binding_id or "").strip()
        if not clean:
            raise ValueError("workspace binding id is required")
        return self._request("POST", f"workspaces/{clean}/unlink", json_body=payload)

    def wiki_pages(self, **payload: Any) -> dict[str, Any]:
        return self._request("GET", "wiki/pages", params=payload)

    def wiki_page(self, wiki_page_id: str, **payload: Any) -> dict[str, Any]:
        clean = validate_wiki_page_id(wiki_page_id)
        return self._request("GET", f"wiki/pages/{clean}", params=payload)

    def create_wiki_draft(self, **payload: Any) -> dict[str, Any]:
        return self._request("POST", "wiki/pages", json_body=payload)

    def verify_wiki_page(self, wiki_page_id: str, **payload: Any) -> dict[str, Any]:
        clean = validate_wiki_page_id(wiki_page_id)
        return self._request("POST", f"wiki/pages/{clean}/verify", json_body=payload)

    def list_logbook_entries(self, project_id: str, **payload: Any) -> dict[str, Any]:
        project = validate_logbook_route_id(project_id, field="project id")
        return self._request(
            "GET", "logbook/entries", params={**payload, "project_id": project}
        )

    def get_logbook_entry(
        self, project_id: str, entry_id: str, **payload: Any
    ) -> dict[str, Any]:
        project = validate_logbook_route_id(project_id, field="project id")
        entry = validate_logbook_route_id(entry_id, field="logbook entry id")
        return self._request(
            "GET",
            f"logbook/entries/{entry}",
            params={**payload, "project_id": project},
        )

    def create_logbook_entry(self, project_id: str, **payload: Any) -> dict[str, Any]:
        project = validate_logbook_route_id(project_id, field="project id")
        return self._request(
            "POST", "logbook/entries", json_body={**payload, "project_id": project},
            success_statuses=(200, 201),
        )

    def memory_snapshot(self, **payload: Any) -> dict[str, Any]:
        return self._request("GET", "memory/snapshot", params=payload)

    def memory_search(self, **payload: Any) -> dict[str, Any]:
        return self._request("GET", "memory/search", params=payload)

    def create_memory_proposal(self, **payload: Any) -> dict[str, Any]:
        return self._request("POST", "memory/proposals", json_body=payload)

    def import_memory_bundle(self, **payload: Any) -> dict[str, Any]:
        return self._request("POST", "memory/import-bundles", json_body=payload)

    def create_bug_report(self, **payload: Any) -> dict[str, Any]:
        return self._request("POST", "bug-reports", json_body=payload)

    def get_bug_report(self, bug_report_id: str, **payload: Any) -> dict[str, Any]:
        clean = str(bug_report_id or "").strip()
        if not clean:
            raise ValueError("bug report id is required")
        return self._request("GET", f"bug-reports/{clean}", params=payload)

    def create_bug_evidence(self, **payload: Any) -> dict[str, Any]:
        return self._request("POST", "bug-evidence", json_body=payload)

    def bug_evidence_search(self, **payload: Any) -> dict[str, Any]:
        return self._request("GET", "bug-evidence/search", params=payload)

    def graph_traverse(self, **payload: Any) -> dict[str, Any]:
        return self._request("GET", "graph/traverse", params=payload)

    def create_diagnosis_report(self, **payload: Any) -> dict[str, Any]:
        return self._request("POST", "diagnosis-reports", json_body=payload)

    def promote_diagnosis_report(
        self, diagnosis_report_id: str, **payload: Any
    ) -> dict[str, Any]:
        clean = str(diagnosis_report_id or "").strip()
        if not clean:
            raise ValueError("diagnosis report id is required")
        return self._request(
            "POST", f"diagnosis-reports/{clean}/promote", json_body=payload
        )

    def project_awareness_status(self, **payload: Any) -> dict[str, Any]:
        return self._request("GET", "project-awareness/status", params=payload)

    def bootstrap_project_awareness(self, **payload: Any) -> dict[str, Any]:
        return self._request("POST", "project-awareness/bootstrap", json_body=payload)

    def pull_jobs(self, **payload: Any) -> dict[str, Any]:
        return self._request("GET", "agent/jobs", params=payload)

    def update_job_status(self, job_id: str, **payload: Any) -> dict[str, Any]:
        return self._request("POST", f"agent/jobs/{job_id}/status", json_body=payload)

    def submit_job_result(self, job_id: str, **payload: Any) -> dict[str, Any]:
        return self._request("POST", f"agent/jobs/{job_id}/result", json_body=payload)

    def artifact_lookup(self, **payload: Any) -> dict[str, Any]:
        return self._request("GET", "artifacts/lookup", params=payload)

    def upload_artifact(self, **payload: Any) -> dict[str, Any]:
        return self._request("POST", "artifacts", json_body=payload)

    def create_graph_import(self, manifest: dict[str, object]) -> GraphImportState:
        """Create or resume the immutable import identified by a v2 manifest."""

        if not isinstance(manifest, dict):
            raise TypeError("graph import manifest must be a dictionary")
        if manifest.get("schema") != "hades.graph_bundle.v2":
            raise ValueError("graph import requires a graph bundle v2 manifest")
        try:
            validate_schema("bundle.schema.json", manifest)
            manifest_bytes = canonical_json_bytes(manifest)
        except GraphContractError as exc:
            raise ValueError(
                "graph import requires a canonical graph bundle v2 manifest"
            ) from exc
        if len(manifest_bytes) > _MAX_GRAPH_MANIFEST_BYTES:
            raise ValueError("graph import manifest must be at most 4 MiB")
        response = self._request_with_status(
            "POST",
            "graph-imports",
            json_body=manifest,
            success_statuses=(200, 201),
            error_codes_by_status=_GRAPH_CREATE_ERROR_CODES,
        )
        result = response.payload
        if not isinstance(result, dict):
            raise HadesBackendError("graph import create response must be an object")
        return _parse_graph_import_response(
            result,
            operation="create",
            http_status=response.status_code,
            response_fields=frozenset({
                "import_id",
                "attempt_generation",
                "validation_status",
                "publication_status",
                "missing_chunk_indexes",
                "expires_at",
            }),
            expected_chunks=len(manifest["chunks"]),
        )

    def upload_graph_chunk(
        self,
        import_id: str,
        index: int,
        body: BinaryIO,
        headers: ChunkHeaders,
    ) -> GraphChunkState:
        """Upload one exact deterministic-gzip member without re-encoding it."""

        clean_import = validate_graph_import_id(import_id)
        if type(index) is not int or not 0 <= index <= _MAX_GRAPH_CHUNK_INDEX:
            raise ValueError("graph chunk index must be between 0 and 511")
        if not isinstance(headers, ChunkHeaders):
            raise TypeError("graph chunk headers must be ChunkHeaders")
        if not hasattr(body, "read"):
            raise TypeError("graph chunk body must be a binary file object")
        wire_body = body.read(headers.compressed_bytes + 1)
        if not isinstance(wire_body, bytes):
            raise TypeError("graph chunk body must produce bytes")
        if len(wire_body) != headers.compressed_bytes:
            raise ValueError("graph chunk body changed after its size was recorded")
        if hashlib.sha256(wire_body).hexdigest() != headers.compressed_sha256:
            raise ValueError("graph chunk body changed after its digest was recorded")
        response = self._request_with_status(
            "PUT",
            f"graph-imports/{clean_import}/chunks/{index}",
            content_body=wire_body,
            headers=headers.as_http_headers(),
            success_statuses=(200, 201),
            error_codes_by_status=_GRAPH_CHUNK_ERROR_CODES,
        )
        result = response.payload
        if not isinstance(result, dict):
            raise HadesBackendError("graph chunk response must be an object")
        state = GraphChunkState.from_mapping(result)
        if state.index != index:
            raise HadesBackendError("graph chunk response index does not match request")
        return state

    def complete_graph_import(
        self,
        import_id: str,
        artifact_graph_version: str,
    ) -> GraphImportState:
        clean_import = validate_graph_import_id(import_id)
        version = str(artifact_graph_version or "").strip()
        if not _SHA256.fullmatch(version):
            raise ValueError(
                "artifact graph version must be a lower-case SHA-256 digest"
            )
        response = self._request_with_status(
            "POST",
            f"graph-imports/{clean_import}/complete",
            json_body={"artifact_graph_version": version},
            success_statuses=(200, 202),
            error_codes_by_status=_GRAPH_COMPLETE_ERROR_CODES,
        )
        result = response.payload
        if not isinstance(result, dict):
            raise HadesBackendError("graph import complete response must be an object")
        return _parse_graph_import_response(
            result,
            operation="complete",
            http_status=response.status_code,
            response_fields=frozenset({
                "import_id",
                "validation_status",
                "publication_status",
                "projection_version",
            }),
            requested_import_id=clean_import,
        )

    def graph_import(self, import_id: str) -> GraphImportState:
        clean_import = validate_graph_import_id(import_id)
        response = self._request_with_status(
            "GET",
            f"graph-imports/{clean_import}",
            success_statuses=(200,),
            error_codes_by_status=_GRAPH_GET_ERROR_CODES,
        )
        result = response.payload
        if not isinstance(result, dict):
            raise HadesBackendError("graph import response must be an object")
        return _parse_graph_import_response(
            result,
            operation="get",
            http_status=response.status_code,
            response_fields=frozenset({
                "import_id",
                "validation_status",
                "publication_status",
                "received_chunks",
                "expected_chunks",
                "missing_chunk_indexes",
                "failure",
                "projection_version",
                "expires_at",
            }),
            requested_import_id=clean_import,
        )

    def graph_verification_summary(self, **payload: Any) -> dict[str, Any]:
        return self._request("GET", "graph/verification-summary", params=payload)

    def create_source_slice(self, **payload: Any) -> dict[str, Any]:
        return self._request("POST", "source-slices", json_body=payload)

    def source_slices(self, **payload: Any) -> dict[str, Any]:
        return self._request("GET", "source-slices", params=payload)

    def create_evidence_pack(self, **payload: Any) -> dict[str, Any]:
        return self._request("POST", "evidence-packs", json_body=payload)

    def evidence_packs(self, **payload: Any) -> dict[str, Any]:
        return self._request("GET", "evidence-packs", params=payload)

    def create_causal_pack(self, **payload: Any) -> dict[str, Any]:
        return self._request("POST", "causal-packs", json_body=payload)

    def causal_packs(self, **payload: Any) -> dict[str, Any]:
        return self._request("GET", "causal-packs", params=payload)

    def causal_pack(self, causal_pack_id: str, **payload: Any) -> dict[str, Any]:
        clean = str(causal_pack_id or "").strip()
        if not clean:
            raise ValueError("causal pack id is required")
        return self._request("GET", f"causal-packs/{clean}", params=payload)

    def replay_causal_pack(self, causal_pack_id: str, **payload: Any) -> dict[str, Any]:
        clean = str(causal_pack_id or "").strip()
        if not clean:
            raise ValueError("causal pack id is required")
        return self._request("POST", f"causal-packs/{clean}/replay", json_body=payload)

    def privacy_export(self, **payload: Any) -> dict[str, Any]:
        return self._request("GET", "privacy/export", params=payload)

    def privacy_delete(self, **payload: Any) -> dict[str, Any]:
        return self._request("POST", "privacy/delete", json_body=payload)

    def privacy_retention_cleanup(self, **payload: Any) -> dict[str, Any]:
        return self._request("POST", "privacy/retention-cleanup", json_body=payload)

    def submit_doctor_report(self, **payload: Any) -> dict[str, Any]:
        return self._request("POST", "doctor/reports", json_body=payload)

    def list_inbox(self, **payload: Any) -> dict[str, Any]:
        return self._request("GET", "persephone/inbox", params=payload)

    def create_inbox_message(self, **payload: Any) -> dict[str, Any]:
        return self._request("POST", "persephone/messages", json_body=payload)

    def iter_persephone_events(
        self,
        *,
        project_id: str,
        target_agent_id: str,
        target_workspace_binding_id: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> Iterator[dict[str, Any]]:
        """Yield a bounded Persephone SSE response.

        This is the raw streaming primitive.  Callers that need polling
        fallback should use ``hades_persephone_transport.iter_persephone_events``.
        Rejected response bodies are deliberately never included in errors.
        """
        project = str(project_id or "").strip()
        target = str(target_agent_id or "").strip()
        binding = (
            str(target_workspace_binding_id).strip()
            if target_workspace_binding_id is not None
            else None
        )
        resume = str(cursor).strip() if cursor is not None else None
        if isinstance(limit, bool):
            raise ValueError("limit must be an integer between 1 and 100")
        bounded_limit = int(limit)
        if not project or not target:
            raise ValueError("project_id and target_agent_id are required")
        if target_workspace_binding_id is not None and not binding:
            raise ValueError(
                "target_workspace_binding_id must be non-blank when provided"
            )
        if cursor is not None and not resume:
            raise ValueError("cursor must be non-blank when provided")
        if not 1 <= bounded_limit <= 100:
            raise ValueError("limit must be between 1 and 100")

        params = _query_params({
            "project_id": project,
            "target_agent_id": target,
            "target_workspace_binding_id": binding,
            "cursor": resume,
            "limit": bounded_limit,
        })
        try:
            with self._client.stream(
                "GET",
                self._url("persephone/events"),
                params=params,
                headers={"Accept": "text/event-stream"},
            ) as response:
                if response.status_code >= 400:
                    status = response.status_code
                    if status in {404, 405, 406, 415, 501}:
                        error_code = "stream_unavailable"
                    elif status in {408, 425, 429} or status >= 500:
                        error_code = "stream_transient"
                    else:
                        error_code = "stream_rejected"
                    raise HadesBackendError(
                        f"Persephone stream failed (HTTP {status})",
                        status_code=status,
                        code=error_code,
                    )
                content_type = (
                    response.headers
                    .get("content-type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                )
                if content_type != "text/event-stream":
                    raise HadesBackendError(
                        "Persephone stream has an invalid content type",
                        code="stream_unavailable",
                    )

                event_id: str | None = None
                event_name = "message"
                data_lines: list[str] = []
                yielded = 0
                block_bytes = 0

                def dispatch() -> tuple[dict[str, Any] | None, bool]:
                    nonlocal event_id, event_name, data_lines
                    current_id, current_name, current_data = (
                        event_id,
                        event_name,
                        data_lines,
                    )
                    event_id, event_name, data_lines = None, "message", []
                    if (
                        not current_data
                        and current_id is None
                        and current_name == "message"
                    ):
                        return None, False
                    if current_name == "stop":
                        return None, True
                    if not current_data:
                        raise HadesBackendError(
                            "Persephone stream contains a malformed event",
                            code="stream_malformed",
                        )
                    try:
                        parsed = json.loads("\n".join(current_data))
                    except (TypeError, ValueError):
                        raise HadesBackendError(
                            "Persephone stream contains malformed JSON",
                            code="stream_malformed",
                        ) from None
                    if not isinstance(parsed, dict):
                        raise HadesBackendError(
                            "Persephone stream event must be a JSON object",
                            code="stream_malformed",
                        )
                    if current_id is not None:
                        if "id" in parsed and str(parsed["id"]) != current_id:
                            raise HadesBackendError(
                                "Persephone stream event has conflicting IDs",
                                code="stream_malformed",
                            )
                        parsed.setdefault("id", current_id)
                    return parsed, False

                for line in _iter_bounded_sse_lines(response):
                    encoded_size = len(line.encode("utf-8"))
                    block_bytes += encoded_size + 1
                    if block_bytes > PERSEPHONE_SSE_MAX_EVENT_BYTES:
                        raise HadesBackendError(
                            "Persephone stream event exceeds the size limit",
                            code="stream_malformed",
                        )
                    if line == "":
                        event, should_stop = dispatch()
                        block_bytes = 0
                        if should_stop:
                            return
                        if event is not None:
                            yield event
                            yielded += 1
                            if yielded >= bounded_limit:
                                return
                        continue
                    if line.startswith(":"):
                        continue
                    field, separator, value = line.partition(":")
                    if separator and value.startswith(" "):
                        value = value[1:]
                    if field == "id":
                        if len(value.encode("utf-8")) > PERSEPHONE_SSE_MAX_FIELD_BYTES:
                            raise HadesBackendError(
                                "Persephone stream ID exceeds the size limit",
                                code="stream_malformed",
                            )
                        if "\x00" in value:
                            raise HadesBackendError(
                                "Persephone stream contains a malformed event ID",
                                code="stream_malformed",
                            )
                        event_id = value
                    elif field == "event":
                        if len(value.encode("utf-8")) > PERSEPHONE_SSE_MAX_FIELD_BYTES:
                            raise HadesBackendError(
                                "Persephone stream event name exceeds the size limit",
                                code="stream_malformed",
                            )
                        event_name = value or "message"
                    elif field == "data":
                        data_lines.append(value)

                event, should_stop = dispatch()
                if not should_stop and event is not None and yielded < bounded_limit:
                    yield event
        except HadesBackendError:
            raise
        except httpx.HTTPError as exc:
            raise HadesBackendError(
                f"Persephone stream transport failed: {redact_secret(str(exc))}",
                code="stream_unavailable",
            ) from exc

    def presence_heartbeat(self, **payload: Any) -> dict[str, Any]:
        result = self._request("POST", "presence/heartbeat", json_body=payload)
        if not isinstance(result, dict):
            raise HadesBackendError("presence heartbeat response must be a JSON object")
        return result

    def presence_list(self, **payload: Any) -> list[Any]:
        result = self._request("GET", "presence", params=payload)
        if not isinstance(result, list):
            raise HadesBackendError("presence list response must be a JSON array")
        return result

    def code_claim_create(self, **payload: Any) -> dict[str, Any]:
        result = self._request("POST", "code-claims", json_body=payload)
        if not isinstance(result, dict):
            raise HadesBackendError("code claim create response must be a JSON object")
        return result

    def code_claim_release(self, claim_id: str, **payload: Any) -> dict[str, Any]:
        clean = str(claim_id or "").strip()
        if not clean:
            raise ValueError("claim id is required")
        result = self._request(
            "POST", f"code-claims/{clean}/release", json_body=payload
        )
        if not isinstance(result, dict):
            raise HadesBackendError("code claim release response must be a JSON object")
        return result

    def code_claim_detect_conflicts(self, **payload: Any) -> list[Any]:
        result = self._request("GET", "code-claims/conflicts", params=payload)
        if not isinstance(result, list):
            raise HadesBackendError(
                "code claim detect conflicts response must be a JSON array"
            )
        return result
