"""Stable, session-owned context for plugin slash commands.

The context deliberately carries interaction capabilities instead of host
objects.  This keeps commands out of the agent transcript and makes a secret
response available only to the handler that requested it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import queue
import time
from typing import Any, Callable


SecretRequest = Callable[[str], str | None]


def _unavailable_secret(_prompt: str) -> str | None:
    return None


def _identity(value: Any) -> Any:
    return value


@dataclass(frozen=True, slots=True)
class PluginCommandContext:
    """Immutable metadata and safe interactive capabilities for one invocation."""

    cwd: Path
    session_id: str
    surface: str
    interactive: bool
    _request_secret: SecretRequest = field(default=_unavailable_secret, repr=False, compare=False)
    _redact: Callable[[Any], Any] = field(default=_identity, repr=False, compare=False)
    _render: Callable[[Any], Any] = field(default=_identity, repr=False, compare=False)
    _revoke: Callable[[], None] = field(default=lambda: None, repr=False, compare=False)

    def request_secret(self, prompt: str) -> str | None:
        """Request a one-shot secret without persisting or exposing its value."""
        if not self.interactive:
            return None
        return self._request_secret(str(prompt))

    def redact(self, value: Any) -> Any:
        """Remove this invocation's secrets before a host boundary is crossed."""
        return self._redact(value)

    def render(self, value: Any) -> Any:
        """Return the legacy value unless a secret was requested, then safe text."""
        return self._render(value)

    def revoke(self) -> None:
        """Disable secret requests once the host has finished this invocation."""
        self._revoke()


def create_plugin_command_context(
    *,
    cwd: str | Path,
    session_id: str,
    surface: str,
    interactive: bool,
    request_secret: SecretRequest | None = None,
) -> PluginCommandContext:
    """Create an invocation-scoped context with a private secret redactor."""
    secrets: list[str] = []
    active = True
    broker = request_secret or _unavailable_secret

    def request(prompt: str) -> str | None:
        if not active:
            return None
        value = broker(prompt)
        if value:
            secrets.append(value)
        return value or None

    def redact(value: Any) -> Any:
        if not secrets:
            return value
        if isinstance(value, str):
            cleaned = value
            for secret in secrets:
                cleaned = cleaned.replace(secret, "[secret]")
            return cleaned
        if isinstance(value, dict):
            return {redact(key): redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, tuple):
            return tuple(redact(item) for item in value)
        return value

    def render(value: Any) -> Any:
        # The established public ABI returns text/None. Preserve arbitrary
        # legacy objects exactly until a secret is requested; after that every
        # host boundary receives one redacted final string.
        return value if not secrets else redact(str(value))

    def revoke() -> None:
        nonlocal active
        active = False

    return PluginCommandContext(
        cwd=Path(cwd).expanduser().resolve(),
        session_id=str(session_id),
        surface=str(surface),
        interactive=bool(interactive),
        _request_secret=request,
        _redact=redact,
        _render=render,
        _revoke=revoke,
    )


def request_cli_plugin_secret(cli: Any, prompt: str) -> str | None:
    """Use the classic CLI's existing masked secret transport without saving.

    The callback used by skill setup persists its result in ``.env``.  Plugin
    commands need the same masked UI but an invocation-only value, so this
    deliberately stops at the response queue.
    """
    if not getattr(cli, "_app", None):
        from hermes_cli.secret_prompt import masked_secret_prompt

        try:
            return masked_secret_prompt(f"{prompt} (hidden, ESC or empty Enter to cancel): ") or None
        except (EOFError, KeyboardInterrupt):
            return None

    response_queue: queue.Queue[str] = queue.Queue()
    cli._secret_state = {
        "var_name": "PLUGIN_SECRET",
        "prompt": prompt,
        "metadata": {},
        "response_queue": response_queue,
    }
    cli._secret_deadline = time.monotonic() + 120
    try:
        if hasattr(cli, "_clear_secret_input_buffer"):
            cli._clear_secret_input_buffer()
        else:
            cli._app.current_buffer.reset()
        cli._app.invalidate()
        while True:
            try:
                return response_queue.get(timeout=1) or None
            except queue.Empty:
                if time.monotonic() >= cli._secret_deadline:
                    return None
    finally:
        cli._secret_state = None
        cli._secret_deadline = 0
        try:
            cli._clear_secret_input_buffer()
            cli._app.invalidate()
        except Exception:
            pass
