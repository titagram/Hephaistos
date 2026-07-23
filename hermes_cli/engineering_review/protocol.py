"""Versioned wire contracts for the bundled engineering review engine."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, cast

from hermes_constants import get_hermes_home


PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 1024 * 1024
MAX_TRANSPORT_BYTES = 4 * MAX_REQUEST_BYTES
MAX_DIAGNOSTICS = 200

EngineCommand = Literal[
    "capture-target",
    "build-prompts",
    "build-test",
    "test-efficacy",
    "check-coverage",
    "resolve-anchors",
    "compose-review",
    "cleanup",
]
CheckStatus = Literal["passed", "failed", "inconclusive"]

ENGINE_COMMANDS = frozenset({
    "capture-target",
    "build-prompts",
    "build-test",
    "test-efficacy",
    "check-coverage",
    "resolve-anchors",
    "compose-review",
    "cleanup",
})
CHECK_STATUSES = frozenset({"passed", "failed", "inconclusive"})
_RESPONSE_KEYS = frozenset({
    "protocolVersion",
    "requestId",
    "status",
    "output",
    "diagnostics",
})
_DIAGNOSTIC_KEYS = frozenset({"code", "message"})


class EngineProtocolError(ValueError):
    """The request or response violates engineering protocol version 1."""


def _required_string(value: object, field: str, *, nonempty: bool = False) -> str:
    if not isinstance(value, str) or (nonempty and not value):
        qualifier = "non-empty " if nonempty else ""
        raise EngineProtocolError(f"{field} must be a {qualifier}string")
    return value


def _record(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EngineProtocolError(f"{field} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise EngineProtocolError(f"{field} keys must be strings")
    return dict(value)


@dataclass(frozen=True, slots=True)
class EngineRequest:
    """An immutable Python representation of one engine invocation."""

    request_id: str
    command: EngineCommand
    workspace: Path
    artifact_root: Path
    input: Mapping[str, Any]
    protocol_version: int = PROTOCOL_VERSION

    @classmethod
    def from_wire(cls, value: object) -> EngineRequest:
        """Parse the exact public caller request shape used by local proxies."""
        request = _record(value, "request")
        expected = {
            "protocolVersion",
            "requestId",
            "command",
            "workspace",
            "artifactRoot",
            "input",
        }
        if request.keys() != expected:
            unknown = request.keys() - expected
            missing = expected - request.keys()
            field = sorted(unknown or missing)[0]
            raise EngineProtocolError(f"invalid request field: {field}")
        if (
            type(request["protocolVersion"]) is not int
            or request["protocolVersion"] != 1
        ):
            raise EngineProtocolError("request requires protocolVersion 1")
        command = request["command"]
        if not isinstance(command, str) or command not in ENGINE_COMMANDS:
            raise EngineProtocolError("command is not supported by protocolVersion 1")
        instance = cls(
            request_id=_required_string(
                request["requestId"], "requestId", nonempty=True
            ),
            command=cast(EngineCommand, command),
            workspace=Path(
                _required_string(request["workspace"], "workspace", nonempty=True)
            ),
            artifact_root=Path(
                _required_string(request["artifactRoot"], "artifactRoot", nonempty=True)
            ),
            input=_record(request["input"], "input"),
        )
        instance.to_wire()
        return instance

    def to_wire(self) -> dict[str, Any]:
        """Validate and convert this request to the protocol's camelCase form."""
        if type(self.protocol_version) is not int or self.protocol_version != 1:
            raise EngineProtocolError("request requires protocolVersion 1")
        request_id = _required_string(self.request_id, "requestId", nonempty=True)
        if not isinstance(self.command, str) or self.command not in ENGINE_COMMANDS:
            raise EngineProtocolError("command is not supported by protocolVersion 1")

        try:
            workspace = Path(self.workspace)
            artifact_root = Path(self.artifact_root)
        except TypeError as exc:
            raise EngineProtocolError(
                "workspace and artifactRoot must be filesystem paths"
            ) from exc
        if not workspace.is_absolute():
            raise EngineProtocolError("workspace must be an absolute path")
        if not artifact_root.is_absolute():
            raise EngineProtocolError("artifactRoot must be an absolute path")

        try:
            workspace = workspace.resolve(strict=False)
            artifact_root = artifact_root.resolve(strict=False)
            reviews_root = (get_hermes_home() / "reviews").resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise EngineProtocolError("request paths could not be resolved") from exc
        try:
            artifact_root.relative_to(reviews_root)
        except ValueError as exc:
            raise EngineProtocolError(
                f"artifactRoot must be inside the Hermes reviews directory: {reviews_root}"
            ) from exc

        input_value = _record(self.input, "input")
        wire = {
            "protocolVersion": PROTOCOL_VERSION,
            "requestId": request_id,
            "command": self.command,
            "workspace": str(workspace),
            "artifactRoot": str(artifact_root),
            "input": input_value,
        }
        try:
            encoded = json.dumps(
                wire,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise EngineProtocolError("request must be JSON-serializable") from exc
        if len(encoded) > MAX_REQUEST_BYTES:
            raise EngineProtocolError("request must not exceed 1 MiB")
        return wire


@dataclass(frozen=True, slots=True)
class EngineDiagnostic:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class EngineResponse:
    """A validated immutable response from the Node engine."""

    request_id: str
    status: CheckStatus
    output: Mapping[str, Any]
    diagnostics: tuple[EngineDiagnostic, ...]
    protocol_version: int = PROTOCOL_VERSION

    def to_wire(self) -> dict[str, Any]:
        """Return the exact response shape transported by a local proxy."""
        return {
            "protocolVersion": self.protocol_version,
            "requestId": self.request_id,
            "status": self.status,
            "output": dict(self.output),
            "diagnostics": [
                {"code": item.code, "message": item.message}
                for item in self.diagnostics
            ],
        }

    @classmethod
    def from_wire(cls, value: object, *, expected_request_id: str) -> EngineResponse:
        response = _record(value, "response")
        unknown = response.keys() - _RESPONSE_KEYS
        missing = _RESPONSE_KEYS - response.keys()
        if unknown:
            raise EngineProtocolError(f"unknown response field: {sorted(unknown)[0]}")
        if missing:
            raise EngineProtocolError(f"missing response field: {sorted(missing)[0]}")
        if (
            type(response["protocolVersion"]) is not int
            or response["protocolVersion"] != PROTOCOL_VERSION
        ):
            raise EngineProtocolError("response requires protocolVersion 1")

        request_id = _required_string(response["requestId"], "requestId", nonempty=True)
        if request_id != expected_request_id:
            raise EngineProtocolError("response requestId does not match the request")
        status = _required_string(response["status"], "status")
        if status not in CHECK_STATUSES:
            raise EngineProtocolError(f"unknown response status: {status!r}")
        output = _record(response["output"], "output")

        raw_diagnostics = response["diagnostics"]
        if not isinstance(raw_diagnostics, list):
            raise EngineProtocolError("diagnostics must be an array")
        if len(raw_diagnostics) > MAX_DIAGNOSTICS:
            raise EngineProtocolError(
                f"diagnostics must contain at most {MAX_DIAGNOSTICS} entries"
            )
        diagnostics: list[EngineDiagnostic] = []
        for index, raw_diagnostic in enumerate(raw_diagnostics):
            diagnostic = _record(raw_diagnostic, f"diagnostics[{index}]")
            if diagnostic.keys() != _DIAGNOSTIC_KEYS:
                raise EngineProtocolError(
                    f"diagnostics[{index}] must contain only code and message"
                )
            diagnostics.append(
                EngineDiagnostic(
                    code=_required_string(
                        diagnostic["code"], f"diagnostics[{index}].code"
                    ),
                    message=_required_string(
                        diagnostic["message"], f"diagnostics[{index}].message"
                    ),
                )
            )

        return cls(
            protocol_version=PROTOCOL_VERSION,
            request_id=request_id,
            status=cast(CheckStatus, status),
            output=output,
            diagnostics=tuple(diagnostics),
        )
