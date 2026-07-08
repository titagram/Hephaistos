"""HTTP client for DevBoard plugin agent work items."""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import httpx

from hermes_cli.hades_backend_client import (
    HadesBackendError,
    _normalize_base_url,
    _query_params,
    redact_secret,
)


PLUGIN_API_PREFIX = "/api/plugin/v1"


class HadesPluginWorkItemsClient:
    """Small synchronous client for the plugin agent-work-items API."""

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
            raise ValueError("plugin API token is required")
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                "User-Agent": "hades-agent/plugin-work-items-client",
            },
        )

    def close(self) -> None:
        self._client.close()

    def _url(self, path: str) -> str:
        clean = "/" + str(path or "").lstrip("/")
        return urljoin(PLUGIN_API_PREFIX.rstrip("/") + "/", clean.lstrip("/"))

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            response = self._client.request(
                method,
                self._url(path),
                json=json_body,
                params=_query_params(params),
                headers=headers,
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
            raise HadesBackendError(f"invalid JSON response from plugin API: {redact_secret(response.text)}") from exc
        if not isinstance(data, dict):
            raise HadesBackendError("plugin API response must be a JSON object")
        return data

    def list_agent_work_items(self, **payload: Any) -> dict[str, Any]:
        return self._request("GET", "agent-work-items", params=payload)

    def auth_check(self) -> dict[str, Any]:
        return self._request("POST", "auth/check")

    def register_device(
        self,
        *,
        name: str,
        fingerprint_hash: str,
        platform_os: str,
        platform_arch: str,
        plugin_version: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "devices/register",
            json_body={
                "name": name,
                "fingerprint_hash": fingerprint_hash,
                "platform_os": platform_os,
                "platform_arch": platform_arch,
                "plugin_version": plugin_version,
            },
        )

    def list_projects(self) -> dict[str, Any]:
        return self._request("GET", "projects")

    def list_repositories(self, project_id: str) -> dict[str, Any]:
        return self._request("GET", f"projects/{project_id}/repositories")

    def register_local_workspace(
        self,
        repository_id: str,
        *,
        device_id: str,
        local_root_hash: str,
        display_path: str,
        current_branch: str,
        last_head_sha: str | None,
        dirty_status: str,
        remote_name: str | None = None,
        remote_url_host: str | None = None,
        remote_url_hash: str | None = None,
        upstream_branch: str | None = None,
        ahead_count: int | None = None,
        behind_count: int | None = None,
        git_state_observed_at: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"repositories/{repository_id}/local-workspaces",
            json_body={
                "local_root_hash": local_root_hash,
                "display_path": display_path,
                "current_branch": current_branch,
                "last_head_sha": last_head_sha,
                "dirty_status": dirty_status,
                "remote_name": remote_name,
                "remote_url_host": remote_url_host,
                "remote_url_hash": remote_url_hash,
                "upstream_branch": upstream_branch,
                "ahead_count": ahead_count,
                "behind_count": behind_count,
                "git_state_observed_at": git_state_observed_at,
            },
            headers={"X-DevBoard-Device-Id": device_id},
        )

    def claim_agent_work_item(self, work_item_id: str, *, local_workspace_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"agent-work-items/{work_item_id}/claim",
            json_body={"local_workspace_id": local_workspace_id},
        )

    def heartbeat_agent_work_item(self, work_item_id: str, *, lease_token: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"agent-work-items/{work_item_id}/heartbeat",
            json_body={"lease_token": lease_token},
        )

    def complete_agent_work_item(
        self,
        work_item_id: str,
        *,
        lease_token: str,
        chat_message: str | None = None,
        memory_entry: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"lease_token": lease_token}
        if chat_message is not None:
            payload["chat_message"] = chat_message
        if memory_entry is not None:
            payload["memory_entry"] = memory_entry
        return self._request("POST", f"agent-work-items/{work_item_id}/complete", json_body=payload)

    def fail_agent_work_item(self, work_item_id: str, *, lease_token: str, message: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"agent-work-items/{work_item_id}/fail",
            json_body={"lease_token": lease_token, "message": message},
        )
