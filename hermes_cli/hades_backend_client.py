"""HTTP client and small contract helpers for the Hades Laravel backend."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterator
from urllib.parse import urljoin

import httpx


API_PREFIX = "/api/hades/v1"
# The envelope permits a 64 KiB payload.  SSE adds bounded metadata around it,
# but no single line or unfinished event block may grow without limit.
PERSEPHONE_SSE_MAX_EVENT_BYTES = 65_536 + 16_384
PERSEPHONE_SSE_MAX_LINE_BYTES = PERSEPHONE_SSE_MAX_EVENT_BYTES
PERSEPHONE_SSE_MAX_FIELD_BYTES = 4_096
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9][A-Za-z0-9_\-]{6,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)(token[=:]\s*)[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)(api[_-]?key[=:]\s*)[A-Za-z0-9._\-]{8,}"),
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
    return token_env_key(base_url, project_id, agent_id).replace("AGENT_TOKEN", "PLUGIN_TOKEN")


def plugin_device_secret_env_key(base_url: str, project_id: str, agent_id: str) -> str:
    """Return the profile-secret env key for the Plugin request-signing secret."""
    return token_env_key(base_url, project_id, agent_id).replace("AGENT_TOKEN", "PLUGIN_DEVICE_SECRET")


def redact_secret(text: Any) -> str:
    """Redact likely backend/API secrets from text before surfacing errors."""
    value = text if isinstance(text, str) else json.dumps(text, sort_keys=True, default=str)
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub(lambda m: (m.group(1) if m.lastindex else "") + "***", value)
    return value


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
        clean = "/" + str(path or "").lstrip("/")
        return urljoin(API_PREFIX.rstrip("/") + "/", clean.lstrip("/"))

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        try:
            response = self._client.request(
                method,
                self._url(path),
                json=json_body,
                params=_query_params(params),
            )
        except httpx.HTTPError as exc:
            raise HadesBackendError(redact_secret(str(exc))) from exc
        if response.status_code >= 400:
            try:
                body: Any = response.json()
            except ValueError:
                body = response.text
            code = None
            next_step = None
            details = None
            if isinstance(body, dict):
                error = body.get("error")
                if isinstance(error, dict):
                    code = str(error.get("code") or "") or None
                    next_step = str(error.get("next_step") or "") or None
                    details = error.get("details")
            raise HadesBackendError(
                f"{response.status_code}: {redact_secret(body)}",
                status_code=response.status_code,
                code=code,
                next_step=next_step,
                details=details,
            )
        if not response.content:
            return {}
        try:
            data = response.json()
        except ValueError as exc:
            raise HadesBackendError(f"invalid JSON response from backend: {redact_secret(response.text)}") from exc
        if not isinstance(data, (dict, list)):
            raise HadesBackendError("backend response must be a JSON object or array")
        return data

    def health(self) -> dict[str, Any]:
        return self._request("GET", "health")

    def capabilities(self) -> dict[str, Any]:
        return self._request("GET", "capabilities")

    def verify_token(self, *, project_id: str) -> dict[str, Any]:
        return self._request("POST", "token/verify", json_body={"project_id": project_id})

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

    def unlink_workspace(self, workspace_binding_id: str, **payload: Any) -> dict[str, Any]:
        clean = str(workspace_binding_id or "").strip()
        if not clean:
            raise ValueError("workspace binding id is required")
        return self._request("POST", f"workspaces/{clean}/unlink", json_body=payload)

    def wiki_pages(self, **payload: Any) -> dict[str, Any]:
        return self._request("GET", "wiki/pages", params=payload)

    def wiki_page(self, wiki_page_id: str, **payload: Any) -> dict[str, Any]:
        clean = str(wiki_page_id or "").strip()
        if not clean:
            raise ValueError("wiki page id is required")
        return self._request("GET", f"wiki/pages/{clean}", params=payload)

    def create_wiki_draft(self, **payload: Any) -> dict[str, Any]:
        return self._request("POST", "wiki/pages", json_body=payload)

    def verify_wiki_page(self, wiki_page_id: str, **payload: Any) -> dict[str, Any]:
        clean = str(wiki_page_id or "").strip()
        if not clean:
            raise ValueError("wiki page id is required")
        return self._request("POST", f"wiki/pages/{clean}/verify", json_body=payload)

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

    def promote_diagnosis_report(self, diagnosis_report_id: str, **payload: Any) -> dict[str, Any]:
        clean = str(diagnosis_report_id or "").strip()
        if not clean:
            raise ValueError("diagnosis report id is required")
        return self._request("POST", f"diagnosis-reports/{clean}/promote", json_body=payload)

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
        result = self._request("POST", f"code-claims/{clean}/release", json_body=payload)
        if not isinstance(result, dict):
            raise HadesBackendError("code claim release response must be a JSON object")
        return result

    def code_claim_detect_conflicts(self, **payload: Any) -> list[Any]:
        result = self._request("GET", "code-claims/conflicts", params=payload)
        if not isinstance(result, list):
            raise HadesBackendError("code claim detect conflicts response must be a JSON array")
        return result
