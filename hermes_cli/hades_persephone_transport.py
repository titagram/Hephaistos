"""Bounded network transport for durable Persephone agent queues.

The module owns no receiver policy.  It only reads bounded events with a safe
polling fallback and advances already-durable outbox records through delivery,
retry, or dead-letter states.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import random
import re
import sqlite3
import time
from typing import Any, Iterator

from hermes_cli.hades_backend_client import HadesBackendClient, HadesBackendError
from hermes_cli.hades_persephone_store import claim_due_outbox, transition_message


_TERMINAL_CLIENT_STATUSES = frozenset({400, 401, 403, 404, 405, 406, 410, 415, 422})
_SAFE_CODE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded exponential retry policy for durable outbox delivery."""

    base: float = 2.0
    maximum: float = 60.0
    jitter: float = 0.2
    max_attempts: int = 5

    def __post_init__(self) -> None:
        for field, value in (
            ("base", self.base),
            ("maximum", self.maximum),
            ("jitter", self.jitter),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise ValueError(f"retry {field} must be a finite number")
        if self.base <= 0:
            raise ValueError("retry base must be positive")
        if self.maximum < self.base:
            raise ValueError("retry maximum must be at least retry base")
        if not 0 <= self.jitter <= 1:
            raise ValueError("retry jitter must be between 0 and 1")
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")

    def delay(self, attempt: int, *, rng: random.Random) -> int:
        exponent = max(0, int(attempt) - 1)
        saturation_exponent = max(
            0,
            math.ceil(math.log2(self.maximum) - math.log2(self.base)),
        )
        nominal = (
            self.maximum
            if exponent >= saturation_exponent
            else self.base * (2**exponent)
        )
        spread = nominal * self.jitter
        lower = nominal - spread
        # Express the capped upper bound without multiplying two large finite
        # floats into infinity.
        upper = nominal + min(self.maximum - nominal, spread)
        varied = rng.uniform(lower, upper)
        return max(1, int(round(min(self.maximum, varied))))


def _queue_params(
    *,
    project_id: str,
    target_agent_id: str,
    target_workspace_binding_id: str | None,
    cursor: str | None,
    limit: int,
) -> dict[str, Any]:
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
        raise ValueError("target_workspace_binding_id must be non-blank when provided")
    if cursor is not None and not resume:
        raise ValueError("cursor must be non-blank when provided")
    if not 1 <= bounded_limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    return {
        "project_id": project,
        "target_agent_id": target,
        "target_workspace_binding_id": binding,
        "cursor": resume,
        "limit": bounded_limit,
    }


def poll_persephone_events(
    client: HadesBackendClient,
    *,
    project_id: str,
    target_agent_id: str,
    target_workspace_binding_id: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Poll one bounded page and return only structurally valid event objects."""
    try:
        result = client.list_inbox(
            **_queue_params(
                project_id=project_id,
                target_agent_id=target_agent_id,
                target_workspace_binding_id=target_workspace_binding_id,
                cursor=cursor,
                limit=limit,
            )
        )
    except HadesBackendError as exc:
        raise _sanitized_backend_error(exc, context="Persephone polling") from exc
    events = result.get("events") if isinstance(result, dict) else None
    if not isinstance(events, list) or not all(
        isinstance(event, dict) for event in events
    ):
        # Never include the rejected backend payload in this exception.
        raise HadesBackendError("Persephone polling response has an invalid event list")
    if len(events) > int(limit):
        raise HadesBackendError(
            "Persephone polling response exceeds the requested limit"
        )
    return events


def iter_persephone_events(
    client: HadesBackendClient,
    *,
    project_id: str,
    target_agent_id: str,
    target_workspace_binding_id: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
) -> Iterator[dict[str, Any]]:
    """Read SSE events, falling back once to polling with the same cursor."""
    params = _queue_params(
        project_id=project_id,
        target_agent_id=target_agent_id,
        target_workspace_binding_id=target_workspace_binding_id,
        cursor=cursor,
        limit=limit,
    )
    try:
        # Buffer at most 100 events so a malformed tail cannot leak a partial
        # batch before fallback replays the same cursor.
        streamed = list(client.iter_persephone_events(**params))
    except HadesBackendError as exc:
        if exc.code not in {
            "stream_unavailable",
            "stream_transient",
            "stream_malformed",
        }:
            raise _sanitized_backend_error(exc, context="Persephone streaming") from exc
        streamed = poll_persephone_events(client, **params)
    yield from streamed


def _error_label(exc: HadesBackendError, *, max_attempts: bool = False) -> str:
    if exc.status_code is None:
        return "transport_error"
    suffix = (
        "max_attempts"
        if max_attempts
        else (
            exc.code if exc.code and _SAFE_CODE.fullmatch(exc.code) else "backend_error"
        )
    )
    return f"http_{exc.status_code}:{suffix}"


def _sanitized_backend_error(
    exc: HadesBackendError,
    *,
    context: str,
) -> HadesBackendError:
    """Retain routing metadata without retaining an untrusted response body."""
    safe_code = (
        exc.code if exc.code and _SAFE_CODE.fullmatch(exc.code) else None
    )
    status = exc.status_code
    qualifier = f" (HTTP {status})" if status is not None else ""
    return HadesBackendError(
        f"{context} failed{qualifier}",
        status_code=status,
        code=safe_code,
    )


def send_due_messages(
    conn: sqlite3.Connection,
    client: HadesBackendClient,
    *,
    now: int | None = None,
    limit: int = 50,
    retry: RetryPolicy | None = None,
    rng: random.Random | None = None,
    project_id: str | None = None,
    sender_agent_id: str | None = None,
) -> dict[str, int]:
    """Claim and deliver due durable messages with bounded retry semantics."""
    policy = retry or RetryPolicy()
    random_source = rng or random.SystemRandom()
    timestamp = int(time.time()) if now is None else int(now)
    counts = {"sent": 0, "retry": 0, "dead_letter": 0}
    for message in claim_due_outbox(
        conn,
        now=timestamp,
        limit=limit,
        project_id=project_id,
        sender_agent_id=sender_agent_id,
    ):
        try:
            client.create_inbox_message(**message.envelope.to_dict())
        except HadesBackendError as exc:
            terminal = exc.status_code in _TERMINAL_CLIENT_STATUSES
            exhausted = message.attempts >= policy.max_attempts
            if terminal or exhausted:
                transition_message(
                    conn,
                    message.message_id,
                    "dead_letter",
                    queue="outbox",
                    now=timestamp,
                    last_error=_error_label(
                        exc, max_attempts=exhausted and not terminal
                    ),
                )
                counts["dead_letter"] += 1
                continue
            delay = policy.delay(message.attempts, rng=random_source)
            transition_message(
                conn,
                message.message_id,
                "retry",
                queue="outbox",
                now=timestamp,
                next_attempt_at=timestamp + delay,
                last_error=_error_label(exc),
            )
            counts["retry"] += 1
            continue
        transition_message(
            conn,
            message.message_id,
            "sent",
            queue="outbox",
            now=timestamp,
            last_error=None,
        )
        counts["sent"] += 1
    return counts


__all__ = [
    "RetryPolicy",
    "iter_persephone_events",
    "poll_persephone_events",
    "send_due_messages",
]
