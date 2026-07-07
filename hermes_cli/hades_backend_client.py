"""HTTP client and small contract helpers for the Hades Laravel backend."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import urljoin

import httpx


API_PREFIX = "/api/hades/v1"
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9][A-Za-z0-9_\-]{6,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)(token[=:]\s*)[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)(api[_-]?key[=:]\s*)[A-Za-z0-9._\-]{8,}"),
)


class HadesBackendError(RuntimeError):
    """Raised when the Hades backend returns an error response."""


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


def redact_secret(text: Any) -> str:
    """Redact likely backend/API secrets from text before surfacing errors."""
    value = text if isinstance(text, str) else json.dumps(text, sort_keys=True, default=str)
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub(lambda m: (m.group(1) if m.lastindex else "") + "***", value)
    return value


def _string_param(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
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
    ) -> dict[str, Any]:
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
            raise HadesBackendError(f"{response.status_code}: {redact_secret(body)}")
        if not response.content:
            return {}
        try:
            data = response.json()
        except ValueError as exc:
            raise HadesBackendError(f"invalid JSON response from backend: {redact_secret(response.text)}") from exc
        if not isinstance(data, dict):
            raise HadesBackendError("backend response must be a JSON object")
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
            },
        )

    def bind_workspace(self, **payload: Any) -> dict[str, Any]:
        return self._request("POST", "workspaces/bind", json_body=payload)

    def unlink_workspace(self, workspace_binding_id: str, **payload: Any) -> dict[str, Any]:
        clean = str(workspace_binding_id or "").strip()
        if not clean:
            raise ValueError("workspace binding id is required")
        return self._request("POST", f"workspaces/{clean}/unlink", json_body=payload)

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

    def create_diagnosis_report(self, **payload: Any) -> dict[str, Any]:
        return self._request("POST", "diagnosis-reports", json_body=payload)

    def promote_diagnosis_report(self, diagnosis_report_id: str, **payload: Any) -> dict[str, Any]:
        clean = str(diagnosis_report_id or "").strip()
        if not clean:
            raise ValueError("diagnosis report id is required")
        return self._request("POST", f"diagnosis-reports/{clean}/promote", json_body=payload)

    def project_awareness_status(self, **payload: Any) -> dict[str, Any]:
        return self._request("GET", "project-awareness/status", params=payload)

    def pull_jobs(self, **payload: Any) -> dict[str, Any]:
        return self._request("GET", "agent/jobs", params=payload)

    def update_job_status(self, job_id: str, **payload: Any) -> dict[str, Any]:
        return self._request("POST", f"agent/jobs/{job_id}/status", json_body=payload)

    def submit_job_result(self, job_id: str, **payload: Any) -> dict[str, Any]:
        return self._request("POST", f"agent/jobs/{job_id}/result", json_body=payload)

    def upload_artifact(self, **payload: Any) -> dict[str, Any]:
        return self._request("POST", "artifacts", json_body=payload)

    def create_source_slice(self, **payload: Any) -> dict[str, Any]:
        return self._request("POST", "source-slices", json_body=payload)

    def source_slices(self, **payload: Any) -> dict[str, Any]:
        return self._request("GET", "source-slices", params=payload)

    def submit_doctor_report(self, **payload: Any) -> dict[str, Any]:
        return self._request("POST", "doctor/reports", json_body=payload)

    def list_inbox(self, **payload: Any) -> dict[str, Any]:
        return self._request("GET", "persephone/inbox", params=payload)

    def create_inbox_message(self, **payload: Any) -> dict[str, Any]:
        return self._request("POST", "persephone/messages", json_body=payload)
