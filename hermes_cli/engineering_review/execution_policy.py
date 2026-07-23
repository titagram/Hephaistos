"""Trusted execution policy for deterministic engineering-review checks.

The model-facing proxy never chooses this policy.  The public ``hermes
review`` process computes it once from the requested target, the existing
terminal backend, and (when needed) the normal Hermes approval callback.  The
authority then injects the immutable decision into executable engine requests.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Mapping


TargetKind = Literal["local", "file", "range", "pr"]
ExecutionMode = Literal["local", "sandbox", "denied"]

_SANDBOX_BACKENDS = frozenset({"docker", "singularity", "modal", "daytona"})
_SAFE_ENV_NAMES = frozenset({
    "PATH",
    "HOME",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TMP",
    "TEMP",
    "TMPDIR",
    "LANG",
    "LANGUAGE",
})


@dataclass(frozen=True, slots=True)
class ExecutionDecision:
    """Authority-owned decision supplied to build/test engine operations."""

    mode: ExecutionMode
    allowed: bool
    sanitized_env: Mapping[str, str]
    network: bool
    reason: str
    backend: str | None = None

    def to_wire(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "allowed": self.allowed,
            "sanitizedEnv": dict(self.sanitized_env),
            "network": self.network,
            "reason": self.reason,
            "backend": self.backend,
        }


def sanitize_execution_env(source: Mapping[str, str]) -> dict[str, str]:
    """Retain runtime essentials while excluding credentials and hooks."""
    return {
        name: value
        for name, value in source.items()
        if name in _SAFE_ENV_NAMES or name.startswith("LC_")
    }


def decide_execution(
    *,
    target_kind: TargetKind,
    sandbox: str | None,
    allow_local: bool,
    environ: Mapping[str, str] | None = None,
) -> ExecutionDecision:
    """Decide whether repository code may execute for this review.

    A remote PR is untrusted.  It may execute only through a configured Hermes
    sandbox backend, or locally after an explicit approval obtained by the
    public process.  Static review remains available when execution is denied.
    """
    if target_kind not in {"local", "file", "range", "pr"}:
        raise ValueError("target_kind must be local, file, range, or pr")
    backend = (sandbox or "").strip().lower() or None
    safe_env = sanitize_execution_env(os.environ if environ is None else environ)

    if target_kind != "pr":
        return ExecutionDecision(
            mode="local",
            allowed=True,
            sanitized_env=safe_env,
            network=True,
            reason="local_review_uses_existing_terminal_policy",
        )
    if backend in _SANDBOX_BACKENDS:
        return ExecutionDecision(
            mode="sandbox",
            allowed=True,
            sanitized_env=safe_env,
            network=False,
            reason="untrusted_remote_code_sandboxed",
            backend=backend,
        )
    if allow_local:
        return ExecutionDecision(
            mode="local",
            allowed=True,
            sanitized_env=safe_env,
            network=True,
            reason="untrusted_remote_code_explicitly_authorized",
        )
    return ExecutionDecision(
        mode="denied",
        allowed=False,
        sanitized_env={},
        network=False,
        reason="untrusted_remote_code_requires_sandbox_or_consent",
    )


def target_kind_for(target: str) -> TargetKind:
    """Classify the public target without inspecting untrusted target content."""
    value = target.strip()
    if value == "local":
        return "local"
    if value.startswith(("https://github.com/", "http://github.com/")) and "/pull/" in value:
        return "pr"
    if ".." in value or value.upper() == "HEAD":
        return "range"
    return "file"
