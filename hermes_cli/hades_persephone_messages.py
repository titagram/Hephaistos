"""Strict wire contract for project-scoped Hades agent messages.

The backend queue is an untrusted transport.  Parsing therefore creates a
deeply immutable value, rejects extension fields, and derives response
authority from the validated request rather than from response payload data.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import math
from types import MappingProxyType
import time
from typing import Any, Mapping

from hermes_cli.hades_backend_client import redact_secret


AGENT_MESSAGE_SCHEMA = "hades.persephone.agent-message.v1"
MAX_PAYLOAD_BYTES = 65_536
MAX_PAYLOAD_PROPERTIES = 128
BACKEND_CAPABILITY = "persephone_agent_queue_v1"


class MessageType(StrEnum):
    INFORMATION_REQUEST = "information_request"
    LOCAL_DECISION = "local_decision"
    INFORMATION_RESPONSE = "information_response"
    STATUS_QUERY = "status_query"
    STATUS_RESPONSE = "status_response"
    CANCEL_REQUEST = "cancel_request"


class EffectClass(StrEnum):
    INFORMATION_READ = "information_read"
    MUTATING = "mutating"


class DecisionStatus(StrEnum):
    """Stable decision states carried inside local-decision payloads."""

    ANSWERED = "answered"
    WAITING_CONFIRMATION = "waiting_confirmation"
    APPROVED = "approved"
    REFUSED = "refused"
    FAILED = "failed"
    CANCELLED = "cancelled"


_FIELDS = frozenset(
    {
        "schema",
        "message_id",
        "correlation_id",
        "causation_id",
        "project_id",
        "sender_agent_id",
        "target_agent_id",
        "target_workspace_binding_id",
        "message_type",
        "effect",
        "capability",
        "remote_task_id",
        "remote_task_version",
        "expires_at",
        "payload",
    }
)
_REQUIRED_FIELDS = _FIELDS - {
    "causation_id",
    "remote_task_id",
    "remote_task_version",
}
_WORKSPACE_CAPABILITIES = frozenset(
    {
        "source_slice",
        "source_search",
        "symbol_lookup",
        "git_metadata",
        "artifact_metadata",
    }
)
_RESPONSE_TYPES = {
    MessageType.INFORMATION_REQUEST: MessageType.INFORMATION_RESPONSE,
    MessageType.STATUS_QUERY: MessageType.STATUS_RESPONSE,
    MessageType.CANCEL_REQUEST: MessageType.LOCAL_DECISION,
}


def _required_string(raw: Mapping[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank string")
    return value.strip()


def _optional_string(raw: Mapping[str, Any], field: str) -> str | None:
    value = raw.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be null or a non-blank string")
    return value.strip()


def _json_value(value: Any, *, field: str = "payload") -> Any:
    """Validate and deep-freeze a JSON value without retaining caller aliases."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field} must contain finite JSON numbers")
        return value
    if isinstance(value, list):
        return tuple(_json_value(item, field=field) for item in value)
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError(f"{field} object keys must be strings")
        return MappingProxyType(
            {key: _json_value(item, field=field) for key, item in value.items()}
        )
    raise ValueError(f"{field} must contain only JSON values")


def _mutable_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _mutable_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_mutable_json(item) for item in value]
    return value


def _payload(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    value = raw.get("payload")
    if not isinstance(value, dict):
        raise ValueError("payload must be a JSON object")
    if len(value) > MAX_PAYLOAD_PROPERTIES:
        raise ValueError(f"payload exceeds the {MAX_PAYLOAD_PROPERTIES}-property limit")
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        # The backend helper provides the common last line of defence if an
        # implementation-specific encoder detail ever reaches this boundary.
        safe = redact_secret(str(exc))
        raise ValueError(f"payload is not valid JSON: {safe}") from None
    if len(encoded) > MAX_PAYLOAD_BYTES:
        raise ValueError(f"payload exceeds the {MAX_PAYLOAD_BYTES}-byte limit")
    frozen = _json_value(value)
    assert isinstance(frozen, Mapping)
    return frozen


@dataclass(frozen=True)
class AgentMessageEnvelope:
    schema: str
    message_id: str
    correlation_id: str
    causation_id: str | None
    project_id: str
    sender_agent_id: str
    target_agent_id: str
    target_workspace_binding_id: str | None
    message_type: MessageType
    effect: EffectClass
    capability: str
    remote_task_id: str | None
    remote_task_version: str | None
    expires_at: int
    payload: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return a detached, JSON-serializable wire representation."""
        return {
            "schema": self.schema,
            "message_id": self.message_id,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "project_id": self.project_id,
            "sender_agent_id": self.sender_agent_id,
            "target_agent_id": self.target_agent_id,
            "target_workspace_binding_id": self.target_workspace_binding_id,
            "message_type": self.message_type.value,
            "effect": self.effect.value,
            "capability": self.capability,
            "remote_task_id": self.remote_task_id,
            "remote_task_version": self.remote_task_version,
            "expires_at": self.expires_at,
            "payload": _mutable_json(self.payload),
        }

    def validate_receiver(
        self,
        *,
        project_id: str,
        agent_id: str,
        workspace_binding_id: str | None = None,
    ) -> None:
        """Bind the envelope to the authenticated local receiver context."""
        if str(project_id).strip() != self.project_id:
            raise ValueError("message project does not match receiver project")
        if str(agent_id).strip() != self.target_agent_id:
            raise ValueError("message target agent does not match receiver agent")
        if self.target_workspace_binding_id is not None:
            if str(workspace_binding_id or "").strip() != self.target_workspace_binding_id:
                raise ValueError("message target workspace does not match receiver workspace")


def parse_envelope(raw: Mapping[str, Any], *, now: int | None = None) -> AgentMessageEnvelope:
    """Parse an untrusted queue object into the immutable v1 envelope."""
    if not isinstance(raw, Mapping):
        raise ValueError("agent message envelope must be an object")
    unknown = set(raw) - _FIELDS
    if unknown:
        raise ValueError(f"unknown envelope fields ({len(unknown)})")
    missing = _REQUIRED_FIELDS - set(raw)
    if missing:
        raise ValueError(f"missing envelope fields: {', '.join(sorted(missing))}")

    schema = _required_string(raw, "schema")
    if schema != AGENT_MESSAGE_SCHEMA:
        raise ValueError("schema is not a supported agent-message contract")
    try:
        message_type = MessageType(raw.get("message_type"))
    except (TypeError, ValueError):
        raise ValueError("message_type is not supported") from None
    try:
        effect = EffectClass(raw.get("effect"))
    except (TypeError, ValueError):
        raise ValueError("effect is not supported") from None

    expires_at = raw.get("expires_at")
    if isinstance(expires_at, bool) or not isinstance(expires_at, int):
        raise ValueError("expires_at must be an integer Unix timestamp")
    current_time = int(time.time()) if now is None else int(now)
    if expires_at <= current_time:
        raise ValueError("agent message has expired")

    capability = _required_string(raw, "capability")
    target_binding = _optional_string(raw, "target_workspace_binding_id")
    if capability in _WORKSPACE_CAPABILITIES and target_binding is None:
        raise ValueError(f"target_workspace_binding_id is required for {capability}")

    return AgentMessageEnvelope(
        schema=schema,
        message_id=_required_string(raw, "message_id"),
        correlation_id=_required_string(raw, "correlation_id"),
        causation_id=_optional_string(raw, "causation_id"),
        project_id=_required_string(raw, "project_id"),
        sender_agent_id=_required_string(raw, "sender_agent_id"),
        target_agent_id=_required_string(raw, "target_agent_id"),
        target_workspace_binding_id=target_binding,
        message_type=message_type,
        effect=effect,
        capability=capability,
        remote_task_id=_optional_string(raw, "remote_task_id"),
        remote_task_version=_optional_string(raw, "remote_task_version"),
        expires_at=expires_at,
        payload=_payload(raw),
    )


def make_response(
    request: AgentMessageEnvelope,
    *,
    message_id: str,
    target_workspace_binding_id: str | None,
    payload: dict[str, Any],
    expires_at: int,
) -> AgentMessageEnvelope:
    """Build a correlated response whose authority comes only from *request*."""
    response_type = _RESPONSE_TYPES.get(request.message_type)
    if response_type is None:
        raise ValueError("make_response requires a request message")
    return parse_envelope(
        {
            "schema": AGENT_MESSAGE_SCHEMA,
            "message_id": message_id,
            "correlation_id": request.correlation_id,
            "causation_id": request.message_id,
            "project_id": request.project_id,
            "sender_agent_id": request.target_agent_id,
            "target_agent_id": request.sender_agent_id,
            "target_workspace_binding_id": target_workspace_binding_id,
            "message_type": response_type.value,
            "effect": EffectClass.INFORMATION_READ.value,
            "capability": request.capability,
            "remote_task_id": request.remote_task_id,
            "remote_task_version": request.remote_task_version,
            "expires_at": expires_at,
            "payload": payload,
        }
    )


__all__ = [
    "AGENT_MESSAGE_SCHEMA",
    "BACKEND_CAPABILITY",
    "MAX_PAYLOAD_BYTES",
    "MAX_PAYLOAD_PROPERTIES",
    "AgentMessageEnvelope",
    "DecisionStatus",
    "EffectClass",
    "MessageType",
    "make_response",
    "parse_envelope",
]
