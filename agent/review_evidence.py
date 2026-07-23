"""Private, harness-authored reviewer transcripts for engineering review."""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from hermes_cli.engineering_review.runs import ReviewRun, ReviewRunError


_RUN_MARKER = re.compile(r"Hermes-Review-Run: ([A-Za-z0-9_-]+)")
_PLAN_MARKER = re.compile(r"Hermes-Review-Plan: ([^\r\n]+)")
_AGENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|auth(?:orization)?|bearer|cookie|credential|env(?:ironment)?|password|"
    r"private[_-]?key|secret|session[_-]?token|access[_-]?token|refresh[_-]?token|token)",
    re.IGNORECASE,
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b((?=[A-Za-z0-9_.-]*(?:api[_-]?key|authorization|cookie|"
    r"credential|password|private[_-]?key|secret|token))"
    r"[A-Za-z][A-Za-z0-9_.-]*)"
    r"(\s*[:=]\s*)(?:bearer\s+)?"
    r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;}\]]+)"
)
_COOKIE_HEADER = re.compile(r"(?im)^([ \t]*cookie[ \t]*:[ \t]*)[^\r\n]*")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]+=*")
_CANCELLED_RESULT = re.compile(
    r"(?i)^\[?\s*(?:(?:tool\s+(?:execution|call)|execution|operation|task)\s+)?"
    r"(?:was\s+)?(?:aborted|canceled|cancelled|interrupted|skipped|stopped)\b"
)
_INTERRUPTED_SKIP = re.compile(
    r"(?i)\b(?:(?:was\s+)?skipped\s+due\s+to\s+(?:a\s+)?(?:user\s+)?interrupt"
    r"|user\s+interrupt(?:ed|ion)?)"
)


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _redact_text(value: str) -> str:
    redacted = _SENSITIVE_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", value
    )
    redacted = _COOKIE_HEADER.sub(lambda match: f"{match.group(1)}[REDACTED]", redacted)
    return _BEARER.sub("Bearer [REDACTED]", redacted)


def _sanitized(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, Mapping):
        return {
            str(key): _sanitized(item)
            for key, item in value.items()
            if _SENSITIVE_KEY.search(str(key)) is None
        }
    if isinstance(value, (list, tuple)):
        return [_sanitized(item) for item in value]
    return _redact_text(str(value))


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, Mapping) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            else:
                parts.append(json.dumps(item, ensure_ascii=False, default=str))
        return "\n".join(parts)
    if isinstance(content, Mapping):
        return json.dumps(content, ensure_ascii=False, default=str)
    return str(content)


def _is_error_result(content: Any) -> bool:
    structured = content
    if isinstance(content, str):
        stripped = content.lstrip()
        if stripped.startswith(("{", "[")):
            try:
                structured = json.loads(content)
            except (TypeError, ValueError):
                structured = None
        else:
            structured = None
    if isinstance(structured, Mapping):
        if structured.get("error") is not None:
            return True
        status = str(structured.get("status") or "").strip().lower()
        if status in {
            "aborted",
            "canceled",
            "cancelled",
            "denied",
            "error",
            "failed",
            "failure",
            "interrupted",
            "skipped",
            "stopped",
            "timeout",
        }:
            return True

    first_line = _content_text(content).lstrip().splitlines()
    first = first_line[0].strip().lower() if first_line else ""
    if _CANCELLED_RESULT.search(first) or _INTERRUPTED_SKIP.search(first):
        return True
    return first.startswith((
        "denied",
        "error:",
        "error executing tool ",
        "exception:",
        "failed:",
        "keyboardinterrupt",
        "permission denied",
        "traceback ",
    ))


def _private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise OSError(f"unsafe reviewer evidence directory: {path}")
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise OSError(f"reviewer evidence directory is not owned by this user: {path}")
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise OSError(f"reviewer evidence directory is not private: {path}")


def _validated_run(parent_session_id: str, prompt: str) -> ReviewRun | None:
    marker_lines = [
        line
        for line in prompt.splitlines()
        if line.startswith(("Hermes-Review-Run:", "Hermes-Review-Plan:"))
    ]
    if len(marker_lines) != 2:
        return None
    run_match = _RUN_MARKER.fullmatch(marker_lines[0])
    plan_match = _PLAN_MARKER.fullmatch(marker_lines[1])
    if run_match is None or plan_match is None:
        return None

    try:
        run = ReviewRun.load(run_match.group(1), session_id=parent_session_id)
        if run.status != "active":
            return None
        supplied_plan = Path(plan_match.group(1))
        canonical_plan = supplied_plan.resolve(strict=False)
        expected_plan = (run.root / "plan.json").resolve(strict=False)
        if (
            not supplied_plan.is_absolute()
            or supplied_plan != canonical_plan
            or canonical_plan != expected_plan
        ):
            return None
        info = expected_plan.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            return None
        if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
            return None
        if stat.S_IMODE(info.st_mode) != 0o600:
            return None
        return run
    except (OSError, ReviewRunError, RuntimeError, ValueError):
        return None


def _parse_arguments(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return raw.strip()
    return raw if raw is not None else {}


def _successful_calls(messages: Any) -> list[tuple[str, str, Any, Any]]:
    if not isinstance(messages, list):
        return []

    calls_by_id: dict[str, tuple[str, str, Any]] = {}
    anonymous: list[tuple[str, str, Any]] = []
    successful: list[tuple[str, str, Any, Any]] = []
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        if message.get("role") == "assistant":
            tool_calls = message.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue
            for index, call in enumerate(tool_calls):
                if not isinstance(call, Mapping):
                    continue
                function = call.get("function")
                if not isinstance(function, Mapping):
                    continue
                name = function.get("name")
                if not isinstance(name, str) or not name:
                    continue
                call_id = call.get("id")
                identity = call_id if isinstance(call_id, str) and call_id else ""
                pending = (
                    identity or f"anonymous-{len(anonymous) + index}",
                    name,
                    function.get("arguments"),
                )
                if identity:
                    calls_by_id[identity] = pending
                else:
                    anonymous.append(pending)
        elif message.get("role") == "tool":
            call_id = message.get("tool_call_id")
            pending = (
                calls_by_id.pop(call_id, None)
                if isinstance(call_id, str) and call_id
                else (anonymous.pop(0) if anonymous else None)
            )
            if pending is None:
                continue
            content = message.get("content", "")
            if not _is_error_result(content):
                successful.append((*pending, content))
    return successful


def _record(
    agent_id: str,
    record_type: str,
    role: str,
    parts: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "agentId": agent_id,
        "agentName": "reviewer",
        "type": record_type,
        "message": {"role": role, "parts": parts},
        "timestamp": _timestamp(),
    }


def _atomic_jsonl(destination: Path, records: list[dict[str, Any]]) -> None:
    data = "".join(
        json.dumps(record, allow_nan=False, ensure_ascii=False, separators=(",", ":"))
        + "\n"
        for record in records
    ).encode("utf-8")
    if destination.exists() or destination.is_symlink():
        info = destination.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise OSError(f"unsafe reviewer evidence destination: {destination}")
        if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
            raise OSError(f"reviewer evidence is not owned by this user: {destination}")

    temporary = destination.parent / (
        f".{destination.name}.{secrets.token_urlsafe(12)}.tmp"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(temporary, flags, 0o600)
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short reviewer evidence write")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
        directory_descriptor = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def write_reviewer_transcript(
    parent_session_id: str, child: Any, result: Mapping[str, Any]
) -> Path | None:
    """Write validated reviewer evidence without exposing a model path input."""
    if getattr(child, "_delegate_role", None) != "reviewer":
        return None
    prompt = getattr(child, "_subagent_goal", None)
    agent_id = getattr(child, "_subagent_id", None)
    if (
        not isinstance(parent_session_id, str)
        or not isinstance(prompt, str)
        or not isinstance(agent_id, str)
        or _AGENT_ID.fullmatch(agent_id) is None
        or not isinstance(result, Mapping)
    ):
        return None
    run = _validated_run(parent_session_id, prompt)
    if run is None:
        return None

    records = [_record(agent_id, "user", "user", [{"text": _redact_text(prompt)}])]
    for call_id, name, raw_arguments, content in _successful_calls(
        result.get("messages")
    ):
        records.append(
            _record(
                agent_id,
                "assistant",
                "model",
                [
                    {
                        "functionCall": {
                            "id": call_id,
                            "name": name,
                            "args": _sanitized(_parse_arguments(raw_arguments)),
                        }
                    }
                ],
            )
        )
        records.append(
            _record(
                agent_id,
                "tool_result",
                "user",
                [
                    {
                        "functionResponse": {
                            "id": call_id,
                            "name": name,
                            "response": {"output": _sanitized(content)},
                        }
                    }
                ],
            )
        )

    final_text = result.get("final_response")
    if isinstance(final_text, str) and final_text:
        records.append(
            _record(
                agent_id,
                "assistant",
                "model",
                [{"text": _redact_text(final_text)}],
            )
        )

    subagents = run.root / "subagents"
    reviewers = subagents / "reviewers"
    _private_directory(subagents)
    _private_directory(reviewers)
    destination = reviewers / f"agent-{agent_id}.jsonl"
    _atomic_jsonl(destination, records)
    return destination
