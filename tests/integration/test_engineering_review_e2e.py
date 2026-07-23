"""Installed components exercised together against real Git and test runners."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import quote

import pytest

from agent.review_evidence import write_reviewer_transcript
from hermes_cli.engineering_review.authority import (
    ReviewAuthority,
    ReviewAuthorityClient,
)
from hermes_cli.engineering_review.evidence import encode_verified_findings
from hermes_cli.engineering_review.execution_policy import decide_execution
from hermes_cli.engineering_review.protocol import EngineRequest
from hermes_cli.engineering_review.runs import ReviewRun
from hermes_cli.engineering_review.terminal_execution import (
    SandboxTerminalExecutor,
)
from toolsets import _HERMES_CORE_TOOLS


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPOSITORY_ROOT / "tests" / "fixtures" / "engineering_review"
ENGINE_TEST_ROOT = REPOSITORY_ROOT / "packages" / "hermes-engineering" / "tests"
GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Engineering Review E2E",
    "GIT_AUTHOR_EMAIL": "review@example.invalid",
    "GIT_COMMITTER_NAME": "Engineering Review E2E",
    "GIT_COMMITTER_EMAIL": "review@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
}


def _git(workspace: Path, *args: str, text: bool = True) -> str | bytes:
    return subprocess.check_output(
        ["git", *args],
        cwd=workspace,
        env=GIT_ENV,
        text=text,
    )


def _overlay(source: Path, destination: Path) -> None:
    for path in source.rglob("*"):
        if (
            not path.is_file()
            or "__pycache__" in path.parts
            or path.suffix in {".pyc", ".pyo"}
        ):
            continue
        relative = path.relative_to(source)
        if relative.name.endswith(".py.fixture"):
            relative = relative.with_name(
                relative.name.removesuffix(".py.fixture") + ".py"
            )
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def _fixture_workspace(kind: str, parent: Path) -> tuple[Path, str]:
    workspace = parent / f"{kind}-workspace"
    shutil.copytree(FIXTURES / kind / "base", workspace)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=workspace, check=True)
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "base fixture"],
        cwd=workspace,
        env=GIT_ENV,
        check=True,
    )
    base_ref = str(_git(workspace, "rev-parse", "HEAD")).strip()
    _overlay(FIXTURES / kind / "head", workspace)
    # Tests are staged while production and the explicit untracked source remain
    # unstaged. The capture therefore exercises all local-diff channels.
    tests = workspace / "tests"
    if tests.exists():
        subprocess.run(["git", "add", "tests"], cwd=workspace, check=True)
    return workspace, base_ref


def _commit_fixture_head(workspace: Path, untracked: str) -> str:
    subprocess.run(["git", "add", "-u"], cwd=workspace, check=True)
    for production in ("feature.py", "feature.ts"):
        if (workspace / production).is_file():
            # shutil preserves template mtimes; same-size fixture edits can
            # otherwise hit Git's racy-clean stat shortcut.
            subprocess.run(
                ["git", "add", "--force", production],
                cwd=workspace,
                check=True,
            )
    # The named source is intentionally left untracked for the separate local
    # capture; all production and test changes form the immutable range used by
    # the efficacy probe.
    assert untracked in str(_git(workspace, "status", "--porcelain=v1"))
    subprocess.run(
        ["git", "commit", "-qm", "review fixture head"],
        cwd=workspace,
        env=GIT_ENV,
        check=True,
    )
    return str(_git(workspace, "rev-parse", "HEAD")).strip()


def _request(
    authority: ReviewAuthority,
    command: str,
    request_id: str,
    input_value: dict[str, object],
) -> EngineRequest:
    return EngineRequest(
        request_id=request_id,
        command=command,  # type: ignore[arg-type]
        workspace=authority.run.workspace,
        artifact_root=authority.run.root,
        input=input_value,
    )


def _invoke(
    authority: ReviewAuthority,
    command: str,
    input_value: dict[str, object],
    *,
    request_id: str | None = None,
):
    return ReviewAuthorityClient(authority.run.session_id).invoke(
        _request(
            authority,
            command,
            request_id or command,
            input_value,
        ),
        timeout=120,
    )


def _reviewer_result(
    *,
    brief: Path,
    diff: Path,
    read_diff: bool,
    findings: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    calls = [
        {
            "id": "brief",
            "function": {
                "name": "read_file",
                "arguments": json.dumps({"file_path": str(brief)}),
            },
        }
    ]
    if read_diff:
        calls.append({
            "id": "diff",
            "function": {
                "name": "read_file",
                "arguments": json.dumps({
                    "file_path": str(diff),
                    "offset": 0,
                    "limit": 100_000,
                }),
            },
        })
    messages: list[dict[str, object]] = [
        {"role": "assistant", "tool_calls": calls},
        *[
            {
                "role": "tool",
                "tool_call_id": call["id"],
                "content": Path(
                    json.loads(call["function"]["arguments"])["file_path"]  # type: ignore[index]
                ).read_text(encoding="utf-8"),
            }
            for call in calls
        ],
    ]
    final = (
        encode_verified_findings(findings)
        if findings is not None
        else "No issues found in the assigned territory."
    )
    return {"messages": messages, "final_response": final}


def _write_reviewer(
    authority: ReviewAuthority,
    prompt: dict[str, object],
    *,
    agent_id: str,
    read_diff: bool,
    findings: list[dict[str, object]] | None = None,
) -> Path:
    prompt_text = str(prompt["text"])
    prompt_dir = authority.run.root / "plan-prompts"
    brief = prompt_dir / f"{quote(str(prompt['key']), safe='')}.brief.md"
    child = SimpleNamespace(
        _delegate_role="reviewer",
        _subagent_goal=prompt_text,
        _subagent_id=agent_id,
    )
    transcript = write_reviewer_transcript(
        authority.run.session_id,
        child,
        _reviewer_result(
            brief=brief,
            diff=authority.run.root / "target.diff",
            read_diff=read_diff,
            findings=findings,
        ),
    )
    assert transcript is not None
    return transcript


def _stub_completion(content: str) -> SimpleNamespace:
    message = SimpleNamespace(
        content=content,
        tool_calls=None,
        reasoning=None,
        reasoning_content=None,
        reasoning_details=None,
        refusal=None,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")],
        model="stub/review-model",
        usage=None,
    )


@pytest.mark.integration
def test_public_hades_review_runs_real_agent_with_stable_prompt_and_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise review_command -> launch_review_chat -> AIAgent without a model API."""
    import run_agent
    import tools.skills_tool as skills_tool
    from agent.skill_commands import build_preloaded_skills_prompt
    from hermes_cli.engineering_review import command
    from hermes_cli.subcommands.review import build_review_parser
    from run_agent import AIAgent

    home = tmp_path / "hermes-home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(
        skills_tool,
        "SKILLS_DIR",
        REPOSITORY_ROOT / "skills" / "software-development",
    )
    workspace, _base_ref = _fixture_workspace("pytest", tmp_path)
    before = _git(workspace, "status", "--porcelain=v1", "-z", text=False)
    before_remote_refs = _git(
        workspace, "for-each-ref", "--format=%(refname):%(objectname)", "refs/remotes"
    )
    requests: list[dict[str, object]] = []
    client = MagicMock()

    def create(**kwargs: object) -> SimpleNamespace:
        requests.append(kwargs)
        return _stub_completion(f"local review turn {len(requests)}")

    client.chat.completions.create.side_effect = create
    tool_definitions = [
        {
            "type": "function",
            "function": {
                "name": "terminal",
                "description": "stub terminal schema",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    monkeypatch.setattr(
        run_agent,
        "get_tool_definitions",
        lambda *_a, **_k: tool_definitions,
    )
    monkeypatch.setattr(run_agent, "check_toolset_requirements", lambda *_a, **_k: {})
    monkeypatch.setattr(run_agent, "OpenAI", lambda *_a, **_k: client)

    observed: dict[str, object] = {}
    lifecycle: list[str] = []

    class StubAuthority:
        def __init__(self, **_kwargs: object) -> None:
            lifecycle.append("created")

        def start_serving(self) -> None:
            lifecycle.append("serving")

        def close(self) -> None:
            lifecycle.append("closed")

    def stub_chat(args: object) -> None:
        session_id = "public-review-e2e"
        skill_prompt, loaded, missing = build_preloaded_skills_prompt(
            list(args.skills),
            task_id=session_id,
        )
        assert loaded == ["requesting-code-review"]
        assert missing == []
        assert "hermes-review-engine capture-target" in skill_prompt

        agent = AIAgent(
            api_key="stub-key",
            base_url="https://model.invalid/v1",
            model="stub/review-model",
            session_id=session_id,
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
        agent.client = client
        args.session_ready_callback(session_id)
        first = agent.run_conversation(
            args.query,
            system_message=skill_prompt,
            task_id=session_id,
        )
        second = agent.run_conversation(
            "Return the local verdict without publishing it.",
            conversation_history=first["messages"],
            task_id=session_id,
        )
        observed["messages"] = second["messages"]

    monkeypatch.setattr(command, "_load_chat_command", lambda: stub_chat)
    monkeypatch.setattr(command, "ReviewAuthority", StubAuthority)
    monkeypatch.setattr(command, "_prune_completed_review_runs", lambda: None)
    monkeypatch.chdir(workspace)
    parser = argparse.ArgumentParser(prog="hades")
    build_review_parser(
        parser.add_subparsers(dest="command"),
        cmd_review=command.review_command,
    )
    args = parser.parse_args(["review", "local", "--effort", "medium"])

    assert args.func(args) == 0
    assert lifecycle == ["created", "serving", "closed"]
    assert len(requests) == 2
    system_prompts = [
        next(
            message["content"]
            for message in request["messages"]
            if message["role"] == "system"
        )
        for request in requests
    ]
    assert system_prompts[0] == system_prompts[1]
    assert [
        json.dumps(request["tools"], sort_keys=True, separators=(",", ":"))
        for request in requests
    ] == [json.dumps(requests[0]["tools"], sort_keys=True, separators=(",", ":"))] * 2
    roles = [
        message["role"]
        for message in observed["messages"]
        if message["role"] != "system"
    ]
    assert all(left != right for left, right in zip(roles, roles[1:]))
    assert _git(workspace, "status", "--porcelain=v1", "-z", text=False) == before
    assert (
        _git(
            workspace,
            "for-each-ref",
            "--format=%(refname):%(objectname)",
            "refs/remotes",
        )
        == before_remote_refs
    )
    assert str(_git(workspace, "remote")).strip() == ""
    assert all("review" not in name for name in _HERMES_CORE_TOOLS)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("kind", "runner", "effective", "inert", "untracked"),
    [
        (
            "pytest",
            "pytest",
            "tests/test_effective.py",
            "tests/test_inert.py",
            "untracked.py",
        ),
        (
            "vitest",
            "vitest",
            "tests/effective.test.ts",
            "tests/inert.test.ts",
            "untracked.ts",
        ),
    ],
)
def test_real_local_review_classifies_test_efficacy_without_mutating_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    runner: str,
    effective: str,
    inert: str,
    untracked: str,
) -> None:
    home = tmp_path / "hermes-home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(home))
    # Vitest's probe worktree must be able to resolve the repository's already
    # installed, offline dependency tree by walking to the monorepo root.
    if kind == "vitest":
        parent = Path(
            tempfile.mkdtemp(prefix=".tmp-engineering-e2e-", dir=ENGINE_TEST_ROOT)
        )
    else:
        parent = tmp_path / "fixtures"
        parent.mkdir()
    try:
        workspace, base_ref = _fixture_workspace(kind, parent)
        head_ref = _commit_fixture_head(workspace, untracked)
        before = _git(workspace, "status", "--porcelain=v1", "-z", text=False)
        with ReviewAuthority(
            workspace=workspace,
            target="local",
            effort="medium",
            session_id=f"e2e-{kind}-local",
        ) as local_authority:
            local_capture = _invoke(
                local_authority,
                "capture-target",
                {"kind": "forged-and-ignored"},
            )
            assert local_capture.status == "passed", json.dumps(
                local_capture.to_wire(), indent=2
            )
            local_diff = (local_authority.run.root / "target.diff").read_text(
                encoding="utf-8"
            )
            assert untracked in local_diff

        with ReviewAuthority(
            workspace=workspace,
            target=f"{base_ref}..{head_ref}",
            effort="medium",
            session_id=f"e2e-{kind}-range",
        ) as authority:
            capture = _invoke(authority, "capture-target", {"kind": "local"})
            assert capture.status == "passed", json.dumps(capture.to_wire(), indent=2)
            assert capture.output["baseRef"] == base_ref
            assert capture.output["headRef"] == head_ref
            diff = (authority.run.root / "target.diff").read_text(encoding="utf-8")
            assert effective in diff
            assert inert in diff

            efficacy = _invoke(
                authority,
                "test-efficacy",
                {
                    "planPath": capture.output["planPath"],
                    "baseRef": base_ref,
                    "runner": runner,
                    "timeoutMs": 60_000,
                },
            )
            assert efficacy.status == "failed", json.dumps(efficacy.to_wire(), indent=2)
            assert efficacy.output["gated"] == [effective], efficacy.to_wire()
            assert efficacy.output["inert"] == [inert]
            assert efficacy.output["inconclusive"] == []
            assert efficacy.output["cleanupFailure"] is None
            assert not list(workspace.glob(".hermes-efficacy-*"))
            cleanup = ReviewAuthorityClient(authority.run.session_id).cleanup(
                authority.run.run_id,
                timeout=60,
            )
            assert cleanup.status == "passed"
        assert _git(workspace, "status", "--porcelain=v1", "-z", text=False) == before
    finally:
        if kind == "vitest":
            shutil.rmtree(parent)


@pytest.mark.integration
def test_mixed_runner_requires_clarification_then_honors_explicit_pytest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "hermes-home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(home))
    workspace, base_ref = _fixture_workspace("mixed", tmp_path)
    head_ref = _commit_fixture_head(workspace, "untracked.py")
    before = _git(workspace, "status", "--porcelain=v1", "-z", text=False)
    with ReviewAuthority(
        workspace=workspace,
        target=f"{base_ref}..{head_ref}",
        effort="medium",
        session_id="e2e-mixed",
    ) as authority:
        capture = _invoke(authority, "capture-target", {"kind": "local"})
        ambiguous = _invoke(
            authority,
            "test-efficacy",
            {
                "planPath": capture.output["planPath"],
                "baseRef": base_ref,
                "runner": "auto",
                "timeoutMs": 60_000,
            },
            request_id="ambiguous",
        )
        assert ambiguous.status == "inconclusive"
        assert ambiguous.diagnostics[0].code == "ambiguous_runner"
        assert set(ambiguous.output["availableRunners"]) == {"pytest", "vitest"}

        selected = _invoke(
            authority,
            "test-efficacy",
            {
                "planPath": capture.output["planPath"],
                "baseRef": base_ref,
                "runner": "pytest",
                "timeoutMs": 60_000,
            },
            request_id="selected",
        )
        assert selected.output["runner"] == "pytest"
        assert selected.output["gated"] == ["tests/test_effective.py"], json.dumps(
            selected.to_wire(), indent=2
        )
    assert _git(workspace, "status", "--porcelain=v1", "-z", text=False) == before


@pytest.mark.integration
def test_review_artifacts_fail_unread_coverage_and_deduplicate_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "hermes-home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(home))
    workspace, base_ref = _fixture_workspace("pytest", tmp_path)
    head_ref = _commit_fixture_head(workspace, "untracked.py")
    before = _git(workspace, "status", "--porcelain=v1", "-z", text=False)
    with ReviewAuthority(
        workspace=workspace,
        target=f"{base_ref}..{head_ref}",
        effort="medium",
        session_id="e2e-artifacts",
    ) as authority:
        capture = _invoke(authority, "capture-target", {"kind": "local"})
        prompts = _invoke(
            authority,
            "build-prompts",
            {"planPath": capture.output["planPath"], "effort": "medium"},
        )
        assert prompts.status == "passed"
        roster = list(prompts.output["prompts"])
        assert len(roster) >= 2

        duplicate_a = {
            "id": "duplicate-a",
            "severity": "high",
            "title": "Selected value is not validated",
            "body": "The selected value should be checked before returning.",
            "path": "feature.py",
            "quotedCode": "return selected",
            "sourceReviewerIds": ["reviewer-a"],
            "verification": "confirmed",
        }
        duplicate_b = {
            **duplicate_a,
            "id": "duplicate-b",
            "title": "  selected VALUE is not validated ",
            "sourceReviewerIds": ["reviewer-b"],
        }
        transcript_a = _write_reviewer(
            authority,
            roster[0],
            agent_id="reviewer-a",
            read_diff=False,
        )
        transcript_b = _write_reviewer(
            authority,
            roster[1],
            agent_id="reviewer-b",
            read_diff=True,
        )
        verifier_prompt = roster[-1]
        _write_reviewer(
            authority,
            verifier_prompt,
            agent_id="finding-verifier",
            read_diff=True,
            findings=[duplicate_a, duplicate_b],
        )

        coverage = _invoke(
            authority,
            "check-coverage",
            {"planPath": capture.output["planPath"]},
        )
        assert coverage.status == "failed"
        assert coverage.output["coverage"]["unopenedAgents"], coverage.to_wire()

        resolved = _invoke(
            authority,
            "resolve-anchors",
            {"findings": [duplicate_a, duplicate_b]},
        )
        assert resolved.status == "passed"
        assert resolved.output["stats"]["deduplicated"] == 1
        assert len(resolved.output["findings"]) == 1

        composed = _invoke(
            authority,
            "compose-review",
            {
                "effort": "medium",
                "buildTestStatus": "passed",
                "testEfficacyStatus": "failed",
                "ciStatus": "not_available",
            },
        )
        assert composed.status == "failed"
        assert composed.output["event"] == "REQUEST_CHANGES"
        assert Path(composed.output["reportPath"]).is_file()
        assert Path(composed.output["verdictPath"]).is_file()
        for transcript in (transcript_a, transcript_b):
            roles = [
                json.loads(line)["message"]["role"]
                for line in transcript.read_text(encoding="utf-8").splitlines()
            ]
            assert all(left != right for left, right in zip(roles, roles[1:]))
        # The review capability is a CLI+skill edge feature, not a new schema
        # paid by every conversation.
        assert all("review" not in name for name in _HERMES_CORE_TOOLS)
    assert _git(workspace, "status", "--porcelain=v1", "-z", text=False) == before


@pytest.mark.integration
@pytest.mark.parametrize(
    ("kind", "runner", "effective", "inert", "untracked"),
    [
        (
            "pytest",
            "pytest",
            "tests/test_effective.py",
            "tests/test_inert.py",
            "untracked.py",
        ),
        (
            "vitest",
            "vitest",
            "tests/effective.test.ts",
            "tests/inert.test.ts",
            "untracked.ts",
        ),
    ],
)
def test_real_docker_sandbox_runs_captured_fixture_through_authority_proxy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    runner: str,
    effective: str,
    inert: str,
    untracked: str,
) -> None:
    """Run real fixture code in the network-disabled Hermes Docker backend.

    Operators opt in with an image containing Node >=22 and the fixed
    ``/opt/hermes-review-dependencies`` layer. Once opted in, an unavailable
    daemon or image is a failing release gate, never a skip.
    """
    from hermes_cli.engineering_review import authority as authority_module
    from tools.terminal_tool import _create_environment

    image = os.environ.get("HERMES_REVIEW_SANDBOX_E2E_IMAGE")
    if not image:
        pytest.skip(
            "real sandbox E2E requires HERMES_REVIEW_SANDBOX_E2E_IMAGE; "
            "the image must pre-provision offline review dependencies"
        )
    try:
        probe = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        image_probe = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.fail(f"Docker sandbox acceptance was requested but unavailable: {exc}")
    assert probe.returncode == 0, (
        "Docker sandbox acceptance was requested but the daemon is unreachable: "
        f"{probe.stderr.strip()}"
    )
    assert image_probe.returncode == 0, (
        "Docker sandbox acceptance was requested but its preprovisioned image "
        f"is unavailable: {image_probe.stderr.strip()}"
    )

    home = tmp_path / "hermes-home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-enter-container")
    monkeypatch.setenv("REVIEW_E2E_SENTINEL", "must-not-enter-container")
    workspace, base_ref = _fixture_workspace(kind, tmp_path)
    head_ref = _commit_fixture_head(workspace, untracked)
    before = _git(workspace, "status", "--porcelain=v1", "-z", text=False)
    decision = decide_execution(
        target_kind="pr",
        sandbox="docker",
        allow_local=False,
    )
    factory_calls: list[dict[str, object]] = []
    container_identities: list[dict[str, str]] = []

    class RecordingEnvironment:
        def __init__(self, inner: object) -> None:
            self.inner = inner

        def execute(self, command: str, **kwargs: object):
            result = self.inner.execute(command, **kwargs)
            identity = dict(self.inner.recovery_identity())
            if identity and identity not in container_identities:
                container_identities.append(identity)
            return result

        def cleanup(self, *, force_remove: bool = False) -> None:
            self.inner.cleanup(force_remove=force_remove)

        def wait_for_cleanup(self, timeout: float = 30.0) -> bool:
            return self.inner.wait_for_cleanup(timeout=timeout)

        @property
        def cleanup_error(self) -> str | None:
            return self.inner.cleanup_error

        def recovery_identity(self):
            return self.inner.recovery_identity()

    def environment_factory(**kwargs: object) -> RecordingEnvironment:
        factory_calls.append(dict(kwargs))
        return RecordingEnvironment(_create_environment(**kwargs))

    class ConfiguredExecutor(SandboxTerminalExecutor):
        def __init__(self, *, run: ReviewRun, decision: object) -> None:
            super().__init__(
                run=run,
                decision=decision,
                config_loader=lambda: {
                    "env_type": "docker",
                    "docker_image": image,
                },
                environment_factory=environment_factory,
            )

    monkeypatch.setattr(
        authority_module,
        "SandboxTerminalExecutor",
        ConfiguredExecutor,
    )
    worktree_path: Path | None = None
    with ReviewAuthority(
        workspace=workspace,
        target=f"{base_ref}..{head_ref}",
        effort="medium",
        session_id=f"sandbox-e2e-{kind}",
        execution_decision=decision,
    ) as authority:
        capture = _invoke(authority, "capture-target", {"kind": "local"})
        assert capture.status == "passed", capture.to_wire()
        worktree_path = Path(str(capture.output["worktreePath"]))
        assert worktree_path.is_dir()
        efficacy = _invoke(
            authority,
            "test-efficacy",
            {
                "planPath": capture.output["planPath"],
                "baseRef": base_ref,
                "runner": runner,
                "timeoutMs": 60_000,
            },
        )
        assert efficacy.status == "failed", json.dumps(efficacy.to_wire(), indent=2)
        assert efficacy.output["gated"] == [effective]
        assert efficacy.output["inert"] == [inert]
        assert efficacy.output["inconclusive"] == []
        assert efficacy.output["cleanupFailure"] is None
        cleanup = ReviewAuthorityClient(authority.run.session_id).cleanup(
            authority.run.run_id,
            timeout=60,
        )
        assert cleanup.status == "passed"

    assert factory_calls
    for call in factory_calls:
        assert call["network"] is False
        assert call["mount_hermes_resources"] is False
        assert call["allow_implicit_env_passthrough"] is False
        container_config = call["container_config"]
        assert container_config["docker_forward_env"] == []
        assert container_config["docker_extra_args"] == []
        assert "OPENAI_API_KEY" not in container_config["docker_env"]
        assert "REVIEW_E2E_SENTINEL" not in container_config["docker_env"]
    assert container_identities
    for identity in container_identities:
        inspect = subprocess.run(
            ["docker", "inspect", identity["containerId"]],
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert inspect.returncode != 0, (
            f"sandbox container survived cleanup: {identity}"
        )
    assert worktree_path is not None and not worktree_path.exists()
    assert _git(workspace, "status", "--porcelain=v1", "-z", text=False) == before
