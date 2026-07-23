from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli.engineering_review.authority import ReviewAuthority
from hermes_cli.engineering_review.execution_policy import (
    decide_execution,
    target_kind_for,
)
from hermes_cli.engineering_review.protocol import EngineResponse
from hermes_cli.engineering_review.terminal_execution import SandboxSource


def test_remote_pr_without_sandbox_or_consent_cannot_execute_code() -> None:
    decision = decide_execution(
        target_kind="pr",
        sandbox=None,
        allow_local=False,
        environ={"PATH": "/bin", "OPENAI_API_KEY": "secret"},
    )

    assert decision.allowed is False
    assert decision.mode == "denied"
    assert decision.reason == "untrusted_remote_code_requires_sandbox_or_consent"
    assert decision.sanitized_env == {}


def test_sandboxed_pr_strips_secrets_and_disables_network_by_default() -> None:
    decision = decide_execution(
        target_kind="pr",
        sandbox="docker",
        allow_local=False,
        environ={
            "PATH": "/bin",
            "LANG": "C.UTF-8",
            "OPENAI_API_KEY": "secret",
            "GITHUB_TOKEN": "secret",
            "NODE_OPTIONS": "--require=bad.js",
        },
    )

    assert decision.allowed is True
    assert decision.mode == "sandbox"
    assert decision.network is False
    assert decision.sanitized_env == {"PATH": "/bin", "LANG": "C.UTF-8"}


def test_explicit_pr_consent_is_local_but_still_secret_free() -> None:
    decision = decide_execution(
        target_kind="pr",
        sandbox="local",
        allow_local=True,
        environ={"PATH": "/bin", "ANTHROPIC_API_KEY": "secret"},
    )

    assert decision.allowed is True
    assert decision.mode == "local"
    assert decision.network is True
    assert decision.sanitized_env == {"PATH": "/bin"}


def test_local_targets_follow_existing_terminal_policy() -> None:
    decision = decide_execution(
        target_kind="range",
        sandbox=None,
        allow_local=False,
        environ={"PATH": "/bin"},
    )

    assert decision.allowed is True
    assert decision.reason == "local_review_uses_existing_terminal_policy"


def test_target_kind_recognizes_github_pr_before_file_fallback() -> None:
    assert target_kind_for("local") == "local"
    assert target_kind_for("HEAD~1..HEAD") == "range"
    assert target_kind_for("changes.diff") == "file"
    assert target_kind_for("https://github.com/o/r/pull/42") == "pr"


def test_authority_replaces_caller_execution_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "hermes"
    workspace = tmp_path / "workspace"
    home.mkdir(mode=0o700)
    workspace.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    decision = decide_execution(
        target_kind="pr",
        sandbox=None,
        allow_local=False,
        environ={"PATH": "/bin"},
    )
    authority = ReviewAuthority(
        workspace=workspace,
        target="https://github.com/o/r/pull/1",
        effort="low",
        session_id="session-1",
        execution_decision=decision,
    )
    seen: dict[str, object] = {}

    class FakeBridge:
        def invoke(self, request: object, **_kwargs: object) -> EngineResponse:
            seen["request"] = request
            return EngineResponse(
                request_id="request-1",
                status="inconclusive",
                output={},
                diagnostics=(),
            )

    authority._bridge = FakeBridge()  # type: ignore[assignment]
    try:
        response = authority._dispatch({
            "version": 1,
            "action": "invoke",
            "request": {
                "protocolVersion": 1,
                "requestId": "request-1",
                "command": "build-test",
                "workspace": str(workspace),
                "artifactRoot": str(authority.run.root),
                "input": {
                    "planPath": str(authority.run.root / "plan.json"),
                    "execution": {
                        "mode": "local",
                        "allowed": True,
                        "sanitizedEnv": {"OPENAI_API_KEY": "stolen"},
                        "network": True,
                        "reason": "caller override",
                        "backend": None,
                    },
                },
            },
            "timeout": 5,
        })
        trusted = seen["request"]
        assert trusted.input["execution"] == decision.to_wire()  # type: ignore[attr-defined]
        assert response["status"] == "inconclusive"
    finally:
        authority.close()


def test_authority_routes_sandboxed_checks_to_terminal_executor_not_host_bridge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "hermes"
    workspace = tmp_path / "workspace"
    home.mkdir(mode=0o700)
    workspace.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    decision = decide_execution(
        target_kind="pr",
        sandbox="docker",
        allow_local=False,
        environ={"PATH": "/bin"},
    )
    authority = ReviewAuthority(
        workspace=workspace,
        target="https://github.com/o/r/pull/1",
        effort="low",
        session_id="session-1",
        execution_decision=decision,
    )
    seen: dict[str, object] = {}

    class HostBridgeMustNotRun:
        def invoke(self, *_args: object, **_kwargs: object) -> EngineResponse:
            raise AssertionError("sandboxed repository code reached the host bridge")

    class FakeSandboxExecutor:
        def invoke(
            self,
            request: object,
            *,
            timeout: float,
            source: SandboxSource | None,
        ) -> EngineResponse:
            seen["request"] = request
            seen["timeout"] = timeout
            seen["source"] = source
            return EngineResponse(
                request_id=request.request_id,  # type: ignore[attr-defined]
                status="passed",
                output={"commands": []},
                diagnostics=(),
            )

        def cancel(self) -> None:
            seen["cancelled"] = True

    authority._bridge = HostBridgeMustNotRun()  # type: ignore[assignment]
    authority._sandbox_executor = FakeSandboxExecutor()  # type: ignore[assignment]
    authority._sandbox_source = SandboxSource(
        worktree=workspace,
        base_ref="1" * 40,
        head_ref="2" * 40,
    )
    try:
        response = authority._dispatch({
            "version": 1,
            "action": "invoke",
            "request": {
                "protocolVersion": 1,
                "requestId": "request-1",
                "command": "build-test",
                "workspace": str(workspace),
                "artifactRoot": str(authority.run.root),
                "input": {
                    "planPath": str(authority.run.root / "plan.json"),
                    "execution": {
                        "mode": "local",
                        "allowed": True,
                        "sanitizedEnv": {"OPENAI_API_KEY": "stolen"},
                        "network": True,
                        "reason": "caller override",
                        "backend": None,
                    },
                },
            },
            "timeout": 5,
        })
        assert seen["timeout"] == 5
        assert response["status"] == "passed"
    finally:
        authority.close()


def test_authority_close_retries_registered_capture_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "hermes"
    workspace = tmp_path / "workspace"
    home.mkdir(mode=0o700)
    workspace.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    authority = ReviewAuthority(
        workspace=workspace,
        target="HEAD~1..HEAD",
        effort="low",
        session_id="session-1",
    )
    calls: list[str] = []

    class FakeBridge:
        def invoke(self, request: object, **_kwargs: object) -> EngineResponse:
            command = request.command  # type: ignore[attr-defined]
            calls.append(command)
            return EngineResponse(
                request_id=request.request_id,  # type: ignore[attr-defined]
                status="passed",
                output=(
                    {"planPath": str(authority.run.root / "plan.json")}
                    if command == "capture-target"
                    else {}
                ),
                diagnostics=(),
            )

    authority._bridge = FakeBridge()  # type: ignore[assignment]
    authority._dispatch({
        "version": 1,
        "action": "invoke",
        "request": {
            "protocolVersion": 1,
            "requestId": "capture-1",
            "command": "capture-target",
            "workspace": str(workspace),
            "artifactRoot": str(authority.run.root),
            "input": {"kind": "range", "range": "HEAD~1..HEAD"},
        },
        "timeout": 5,
    })

    authority.close()

    assert calls == ["capture-target", "cleanup"]
    assert authority.run.status == "complete"
